from enum import Enum, auto
from dataclasses import dataclass
from pathlib import Path
from typing import List
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


@dataclass
class ErroLexico:
    mensagem: str
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
    MAX_STRING = 1024

    def __init__(self, codigo: str):
        self.codigo = codigo
        self.i = 0
        self.linha = 1
        self.coluna = 1
        self.erros: List[ErroLexico] = []

    def registrar_erro(self, mensagem: str, linha: int, coluna: int):
        self.erros.append(ErroLexico(mensagem, linha, coluna))

    def fim(self):
        return self.i >= len(self.codigo)

    def ver(self, k=0):
        j = self.i + k
        return self.codigo[j] if j < len(self.codigo) else "\0"

    def avancar(self):
        c = self.ver()
        if not self.fim():
            self.i += 1
            if c == "\n":
                self.linha += 1
                self.coluna = 1
            else:
                self.coluna += 1
        return c

    def ler_enquanto(self, condicao):
        lexema = []
        while condicao(self.ver()):
            lexema.append(self.avancar())
        return "".join(lexema)

    def tokenizar(self):
        tokens: List[Token] = []

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
                token = self.ler_identificador()
                if token is not None:
                    tokens.append(token)

            elif c == "_":
                lexema = self.ler_enquanto(lambda x: x.isalnum() or x == "_")
                self.registrar_erro(
                    f"Identificador inválido iniciado por '_': {lexema}",
                    linha,
                    coluna,
                )

            elif c == '"':
                token = self.ler_string()
                if token is not None:
                    tokens.append(token)

            else:
                token = self.ler_simbolo()
                if token is not None:
                    tokens.append(token)

        tokens.append(Token(TipoToken.EOF, "", self.linha, self.coluna))
        return tokens

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

        self.registrar_erro("EOF dentro de comentário de bloco", linha, coluna)

    def ler_identificador(self):
        linha, coluna = self.linha, self.coluna
        lexema = self.ler_enquanto(lambda c: c.isalnum() or c == "_")

        if lexema == "self":
            return Token(TipoToken.SELF, lexema, linha, coluna)

        if lexema == "SELF_TYPE":
            return Token(TipoToken.SELF_TYPE, lexema, linha, coluna)

        if lexema[:1] == "t" and lexema[1:].lower() == "rue":
            return Token(TipoToken.TRUE, lexema, linha, coluna)

        if lexema[:1] == "f" and lexema[1:].lower() == "alse":
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
        self.avancar()  # consome a aspas inicial
        lexema = []
        escapes = {"b": "\b", "t": "\t", "n": "\n", "f": "\f"}

        while not self.fim():
            c = self.ver()

            if c == '"':
                self.avancar()
                texto = "".join(lexema)

                if len(texto) > self.MAX_STRING:
                    self.registrar_erro(
                        "String excede o tamanho máximo permitido",
                        linha,
                        coluna,
                    )
                    return None

                return Token(TipoToken.STR_CONST, texto, linha, coluna)

            if c == "\n":
                self.registrar_erro(
                    "Quebra de linha não escapada em string",
                    linha,
                    coluna,
                )
                return None

            if c == "\0":
                self.registrar_erro(
                    "Caractere nulo dentro de string",
                    self.linha,
                    self.coluna,
                )
                return None

            if c == "\\":
                self.avancar()  # consome a barra

                if self.fim():
                    self.registrar_erro("EOF dentro de string", linha, coluna)
                    return None

                char_lido = self.avancar()

                if char_lido == "0":
                    self.registrar_erro(
                        "String contém caractere nulo escapado",
                        self.linha,
                        self.coluna - 1,
                    )
                    return None

                char_escape = escapes.get(char_lido, char_lido)
                lexema.append(char_escape)

            else:
                lexema.append(self.avancar())

        self.registrar_erro("EOF dentro de string", linha, coluna)
        return None

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
            self.registrar_erro(
                "Terminador de comentário sem abertura: '*)'",
                linha,
                coluna,
            )
            self.avancar()
            self.avancar()
            return None

        c = self.avancar()
        if c in self.SIMBOLOS:
            return Token(self.SIMBOLOS[c], c, linha, coluna)

        self.registrar_erro(f"Caractere ilegal: {repr(c)}", linha, coluna)
        return None


def salvar_saida(caminho_codigo, tokens, erros):
    caminho_saida = Path(caminho_codigo).with_suffix(".lex.json")

    saida = {
        "arquivo_codigo": caminho_codigo,
        "quantidade_tokens": len(tokens),
        "quantidade_erros_lexicos": len(erros),
        "tokens": [
            {
                "tipo": t.tipo.name,
                "lexema": t.lexema,
                "linha": t.linha,
                "coluna": t.coluna,
            }
            for t in tokens
        ],
        "erros_lexicos": [
            {
                "mensagem": e.mensagem,
                "linha": e.linha,
                "coluna": e.coluna,
            }
            for e in erros
        ],
    }

    caminho_saida.write_text(
        json.dumps(saida, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return caminho_saida


def main():
    if len(sys.argv) != 2:
        print("Uso: python lexer_cool.py <arquivo.cl>")
        sys.exit(1)

    caminho_codigo = sys.argv[1]
    codigo = Path(caminho_codigo).read_text(encoding="utf-8")

    lexer = Lexer(codigo)
    tokens = lexer.tokenizar()
    caminho_saida = salvar_saida(caminho_codigo, tokens, lexer.erros)

    print(f"Saída léxica gravada em: {caminho_saida}")
    print(f"Tokens reconhecidos: {len(tokens)}")
    print(f"Erros léxicos: {len(lexer.erros)}")


if __name__ == "__main__":
    main()