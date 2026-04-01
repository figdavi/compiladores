from enum import Enum, auto
from dataclasses import dataclass
from pathlib import Path
import json
import sys


class TipoToken(Enum):
    CLASS = auto()
    ELSE = auto()
    FALSE = auto()
    FI = auto()
    IF = auto()
    IN = auto()
    INHERITS = auto()
    ISVOID = auto()
    LET = auto()
    LOOP = auto()
    POOL = auto()
    THEN = auto()
    WHILE = auto()
    CASE = auto()
    ESAC = auto()
    NEW = auto()
    OF = auto()
    NOT = auto()
    TRUE = auto()

    TYPE_ID = auto()
    OBJECT_ID = auto()
    SELF = auto()
    SELF_TYPE = auto()
    INT_CONST = auto()
    STR_CONST = auto()

    LBRACE = auto()
    RBRACE = auto()
    LPAREN = auto()
    RPAREN = auto()
    COLON = auto()
    SEMICOLON = auto()
    COMMA = auto()
    DOT = auto()
    AT = auto()
    ASSIGN = auto()
    DARROW = auto()
    PLUS = auto()
    MINUS = auto()
    MULT = auto()
    DIV = auto()
    TILDE = auto()
    LT = auto()
    LE = auto()
    EQ = auto()

    EOF = auto()


@dataclass
class Token:
    tipo: TipoToken
    lexema: str
    linha: int
    coluna: int


class Lexer:
    PALAVRAS_RESERVADAS = {
        "class": TipoToken.CLASS,
        "else": TipoToken.ELSE,
        "fi": TipoToken.FI,
        "if": TipoToken.IF,
        "in": TipoToken.IN,
        "inherits": TipoToken.INHERITS,
        "isvoid": TipoToken.ISVOID,
        "let": TipoToken.LET,
        "loop": TipoToken.LOOP,
        "pool": TipoToken.POOL,
        "then": TipoToken.THEN,
        "while": TipoToken.WHILE,
        "case": TipoToken.CASE,
        "esac": TipoToken.ESAC,
        "new": TipoToken.NEW,
        "of": TipoToken.OF,
        "not": TipoToken.NOT,
    }

    SIMBOLOS = {
        "{": TipoToken.LBRACE,
        "}": TipoToken.RBRACE,
        "(": TipoToken.LPAREN,
        ")": TipoToken.RPAREN,
        ":": TipoToken.COLON,
        ";": TipoToken.SEMICOLON,
        ",": TipoToken.COMMA,
        ".": TipoToken.DOT,
        "@": TipoToken.AT,
        "+": TipoToken.PLUS,
        "-": TipoToken.MINUS,
        "*": TipoToken.MULT,
        "/": TipoToken.DIV,
        "~": TipoToken.TILDE,
        "=": TipoToken.EQ,
    }

    BRANCOS = " \n\t\r\f\v"

    def __init__(self, codigo: str):
        self.codigo = codigo
        self.i = 0
        self.linha = 1
        self.coluna = 1

    def fim(self):  # Verifica se chegou ao fim do arquivo.
        return self.i >= len(self.codigo)

    def ver(self, k=0):  # Olha o caractere atual, ou alguns à frente, sem avançar.
        j = self.i + k
        return self.codigo[j] if j < len(self.codigo) else "\0"

    def avancar(self):  # Esse método consome o caractere atual e move o cursor.
        c = self.ver()
        if not self.fim():
            self.i += 1
            if c == "\n":
                self.linha += 1
                self.coluna = 1
            else:
                self.coluna += 1
        return c

    def ler_enquanto(
        self, condicao
    ):  # Lê vários caracteres seguidos enquanto uma condição for verdadeira.
        lexema = []
        while condicao(self.ver()):
            lexema.append(self.avancar())
        return "".join(lexema)

    def tokenizar(self):
        tokens = []
        while not self.fim():
            self.pular_espacos_e_comentarios()
            if self.fim():
                break

            linha, coluna = self.linha, self.coluna
            c = self.ver()

            if c.isdigit():
                lexema = self.ler_enquanto(str.isdigit)
                tokens.append(Token(TipoToken.INT_CONST, lexema, linha, coluna))
            elif c.isalpha():
                tokens.append(self.ler_identificador())
            elif c == "_":
                lexema = self.ler_enquanto(lambda x: x.isalnum() or x == "_")
                sys.exit()
            elif c == '"':
                tokens.append(self.ler_string())
            else:
                tokens.append(self.ler_simbolo())

        tokens.append(Token(TipoToken.EOF, "", self.linha, self.coluna))
        return tokens

    # Remove espaços e comentários antes de procurar o próximo token.
    def pular_espacos_e_comentarios(self):
        while not self.fim():
            if self.ver() in self.BRANCOS:
                self.avancar()
            elif self.ver() == "-" and self.ver(1) == "-":
                while not self.fim() and self.ver() != "\n":
                    self.avancar()
            elif self.ver() == "(" and self.ver(1) == "*":
                self.pular_comentario_bloco()
            else:
                break

    # Comentário de bloco em Cool pode ser aninhado.
    def pular_comentario_bloco(self):
        linha, coluna = self.linha, self.coluna
        self.avancar()
        self.avancar()
        nivel = 1
        while not self.fim():
            if self.ver() == "(" and self.ver(1) == "*":
                self.avancar()
                self.avancar()
                nivel += 1
            elif self.ver() == "*" and self.ver(1) == ")":
                self.avancar()
                self.avancar()
                nivel -= 1
                if nivel == 0:
                    return
            else:
                self.avancar()
        sys.exit()

    def ler_identificador(self):
        linha, coluna = self.linha, self.coluna
        lexema = self.ler_enquanto(lambda c: c.isalnum() or c == "_")

        if lexema == "self":
            return Token(TipoToken.SELF, lexema, linha, coluna)
        if lexema == "SELF_TYPE":
            return Token(TipoToken.SELF_TYPE, lexema, linha, coluna)
        if lexema.lower() == "true":
            return Token(TipoToken.TRUE, lexema, linha, coluna)
        if lexema.lower() == "false":
            return Token(TipoToken.FALSE, lexema, linha, coluna)

        tipo = self.PALAVRAS_RESERVADAS.get(lexema.lower())
        if tipo:
            return Token(tipo, lexema, linha, coluna)
        return Token(
            TipoToken.TYPE_ID if lexema[0].isupper() else TipoToken.OBJECT_ID,
            lexema,
            linha,
            coluna,
        )

    def ler_string(self):
        linha, coluna = self.linha, self.coluna
        self.avancar()
        lexema = []
        escapes = {"b": "\b", "t": "\t", "n": "\n", "f": "\f"}

        while not self.fim():
            c = self.ver()
            if c == '"':
                self.avancar()
                texto = "".join(lexema)
                return Token(TipoToken.STR_CONST, texto, linha, coluna)
            if c == "\n":
                sys.exit()
            if c == "\\":
                self.avancar()
                if self.fim():
                    break
                char_escape = escapes.get(self.avancar(), self.codigo[self.i - 1])
                lexema.append(char_escape)
            else:
                lexema.append(self.avancar())
        sys.exit()

    def ler_simbolo(self):
        linha, coluna = self.linha, self.coluna

        if self.ver() == "<" and self.ver(1) == "-":
            self.avancar()
            self.avancar()
            return Token(TipoToken.ASSIGN, "<-", linha, coluna)

        if self.ver() == "<" and self.ver(1) == "=":
            self.avancar()
            self.avancar()
            return Token(TipoToken.LE, "<=", linha, coluna)

        if self.ver() == "=" and self.ver(1) == ">":
            self.avancar()
            self.avancar()
            return Token(TipoToken.DARROW, "=>", linha, coluna)

        if self.ver() == "<":
            self.avancar()
            return Token(TipoToken.LT, "<", linha, coluna)

        if self.ver() == "*" and self.ver(1) == ")":
            sys.exit()

        c = self.avancar()
        if c in self.SIMBOLOS:
            return Token(self.SIMBOLOS[c], c, linha, coluna)

        sys.exit()


def salvar_saida(caminho_codigo, tokens):
    caminho_saida = Path(caminho_codigo).with_suffix(".lex.json")
    saida = {
        "arquivo_codigo": caminho_codigo,
        "quantidade_tokens": len(tokens),
        "tokens": [
            {
                "tipo": t.tipo.name,
                "lexema": t.lexema,
                "linha": t.linha,
                "coluna": t.coluna,
            }
            for t in tokens
        ],
    }
    caminho_saida.write_text(
        json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return caminho_saida


def main():
    if len(sys.argv) != 2:
        print("Uso: python lexer_cool.py <arquivo.cl>")
        sys.exit()

    caminho_codigo = sys.argv[1]
    lexer = Lexer(Path(caminho_codigo).read_text(encoding="utf-8"))
    tokens = lexer.tokenizar()
    caminho_saida = salvar_saida(caminho_codigo, tokens)

    print(f"Saída léxica gravada em: {caminho_saida}")
    print(f"Tokens reconhecidos: {len(tokens)}")


if __name__ == "__main__":
    main()
