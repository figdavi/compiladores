from pathlib import Path
import json
import sys

from lexer_cool import Lexer, TipoToken


# =========================
# Erro sintático
# =========================


class ErroSintatico(Exception):
    def __init__(self, mensagem, token):
        self.mensagem = mensagem
        self.linha = token.linha
        self.coluna = token.coluna
        self.token_encontrado = f"{token.tipo.name} ({token.lexema!r})"
        super().__init__(str(self))

    def __str__(self):
        return (
            f"Erro sintático na linha {self.linha}, coluna {self.coluna}: "
            f"{self.mensagem}. Encontrado: {self.token_encontrado}"
        )

    def para_dict(self):
        return {
            "mensagem": self.mensagem,
            "linha": self.linha,
            "coluna": self.coluna,
            "token_encontrado": self.token_encontrado,
        }


# Ela representa a estrutura do programa Cool em formato de árvore
def no(tipo_no, token, **campos):
    """Cria um nó simples da AST."""
    return {
        "no": tipo_no,
        "linha": token.linha,
        "coluna": token.coluna,
        **campos,
    }


# =========================
# Parser Cool
# =========================


class ParserCool:
    # Precedência da Cool: quanto maior, mais forte
    PREC = {
        TipoToken.ASSIGN: 10,
        TipoToken.LT: 30,
        TipoToken.LE: 30,
        TipoToken.EQ: 30,
        TipoToken.PLUS: 40,
        TipoToken.MINUS: 40,
        TipoToken.MULT: 50,
        TipoToken.DIV: 50,
    }

    # Trata operadores que aparecem antes da expressão
    UNARIOS = {
        TipoToken.NOT: (20, "Not"),
        TipoToken.ISVOID: (60, "Isvoid"),
        TipoToken.TILDE: (70, "NegacaoAritmetica"),
    }

    # Impede comparações encadeadas
    COMPARACOES = {
        TipoToken.LT,
        TipoToken.LE,
        TipoToken.EQ,
    }

    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0

    # -------------------------
    # Funções básicas
    # -------------------------

    def atual(self):
        return self.tokens[self.i]

    def fim(self):
        return self.atual().tipo == TipoToken.EOF

    def avancar(self):
        token = self.atual()

        if not self.fim():
            self.i += 1

        return token

    def aceitar(self, tipo):
        if self.atual().tipo == tipo:
            return self.avancar()

        return None

    def consumir(self, tipo, mensagem):
        if self.atual().tipo == tipo:
            return self.avancar()

        self.erro(mensagem)

    def erro(self, mensagem, token=None):
        raise ErroSintatico(mensagem, token or self.atual())

    # -------------------------
    # Programa e classes
    # -------------------------

    # Ler uma ou mais classes até chegar ao EOF
    def parse(self):
        if self.fim():
            self.erro("Programa vazio; era esperada ao menos uma classe")

        inicio = self.atual()
        classes = []

        while not self.fim():
            classes.append(self.classe())
            self.consumir(
                TipoToken.SEMICOLON,
                "Esperado ';' após a definição da classe",
            )

        return no("Programa", inicio, classes=classes)

    # Lê uma classe Cool com ou sem herença
    def classe(self):
        inicio = self.consumir(
            TipoToken.CLASS,
            "Esperado 'class' no início da classe",
        )

        nome = self.consumir(
            TipoToken.TYPE_ID,
            "Esperado nome da classe",
        )

        pai = None

        if self.aceitar(TipoToken.INHERITS):
            pai = self.tipo(
                aceita_self_type=False,
                mensagem="SELF_TYPE não pode ser usado como classe pai",
            )

        self.consumir(TipoToken.LBRACE, "Esperado '{' no corpo da classe")

        features = []

        # Esse loop lê tudo que existe dentro da classe
        while self.atual().tipo != TipoToken.RBRACE:
            if self.fim():
                self.erro("Esperado '}' ao final da classe")

            features.append(self.feature())

            self.consumir(
                TipoToken.SEMICOLON,
                "Esperado ';' após a feature",
            )

        self.consumir(TipoToken.RBRACE, "Esperado '}' ao final da classe")

        return no(
            "Classe",
            inicio,
            nome=nome.lexema,
            pai=pai,
            features=features,
        )

    def tipo(self, aceita_self_type=True, mensagem="Esperado um tipo"):
        token = self.atual()

        if token.tipo == TipoToken.TYPE_ID:
            self.avancar()
            return token.lexema

        if aceita_self_type and token.tipo == TipoToken.SELF_TYPE:
            self.avancar()
            return token.lexema

        self.erro(mensagem)

    # -------------------------
    # Features
    # -------------------------

    # Descobrir se está lendo é um atributo ou um método
    def feature(self):
        nome = self.consumir(
            TipoToken.OBJECT_ID,
            "Esperado nome de feature",
        )

        # Método: nome(formais) : Tipo { expr }
        if self.aceitar(TipoToken.LPAREN):
            formais = self.lista(TipoToken.RPAREN, self.formal)

            self.consumir(TipoToken.RPAREN, "Esperado ')' após os parâmetros")
            self.consumir(TipoToken.COLON, "Esperado ':' antes do tipo de retorno")

            tipo_retorno = self.tipo(aceita_self_type=True)

            self.consumir(TipoToken.LBRACE, "Esperado '{' antes do corpo do método")
            corpo = self.expr()
            self.consumir(TipoToken.RBRACE, "Esperado '}' após o corpo do método")

            return no(
                "Metodo",
                nome,
                nome=nome.lexema,
                formais=formais,
                tipo_retorno=tipo_retorno,
                corpo=corpo,
            )

        # Atributo: nome : Tipo [ <- expr ]
        self.consumir(
            TipoToken.COLON,
            "Esperado '(' para método ou ':' para atributo",
        )

        tipo = self.tipo(aceita_self_type=True)
        inicializacao = self.expr() if self.aceitar(TipoToken.ASSIGN) else None

        return no(
            "Atributo",
            nome,
            nome=nome.lexema,
            tipo=tipo,
            inicializacao=inicializacao,
        )

    def formal(self):
        nome = self.consumir(
            TipoToken.OBJECT_ID,
            "Esperado nome do parâmetro formal",
        )

        self.consumir(
            TipoToken.COLON,
            "Esperado ':' após o parâmetro formal",
        )

        tipo = self.tipo(
            aceita_self_type=False,
            mensagem="SELF_TYPE não pode ser usado como tipo de parâmetro formal",
        )

        return no(
            "Formal",
            nome,
            nome=nome.lexema,
            tipo=tipo,
        )

    def lista(self, fim, parse_item):
        itens = []

        if self.atual().tipo == fim:
            return itens

        itens.append(parse_item())

        while self.aceitar(TipoToken.COMMA):
            itens.append(parse_item())

        return itens

    # -------------------------
    # Expressões
    # -------------------------

    """Ela é chamada: 
            no corpo de um método
            na inicialização de um atributo
            dentro de um if
            dentro de um while
            dentro de um let
            dentro de um case
            nos argumentos de chamadas de método"""

    def expr(self):
        return self.expr_prec(0)  # a + b * c

    def expr_prec(self, min_prec):
        # Começa lendo número, string, identificador, if, while, let, case, new, not, isvoid, etc.
        esquerda = self.expr_prefixa()

        while True:
            token = self.atual()

            # Dispatch comum: expr.metodo(...)
            if token.tipo == TipoToken.DOT and 90 >= min_prec:
                esquerda = self.dispatch(esquerda, estatico=False)
                continue

            # Dispatch estático: expr@Tipo.metodo(...)
            if token.tipo == TipoToken.AT and 80 >= min_prec:
                esquerda = self.dispatch(esquerda, estatico=True)
                continue

            if token.tipo not in self.PREC:
                break

            # Precedência do operador
            prec = self.PREC[token.tipo]

            if prec < min_prec:
                break

            op = self.avancar()

            # Atribuição associa à direita
            if op.tipo == TipoToken.ASSIGN:
                if esquerda["no"] != "Identificador":
                    self.erro(
                        "Atribuição exige um identificador no lado esquerdo",
                        op,
                    )

                if esquerda["nome"] == "self":
                    self.erro("Não é permitido atribuir a 'self'", op)

                direita = self.expr_prec(prec)

                esquerda = no(
                    "Atribuicao",
                    op,
                    nome=esquerda["nome"],
                    valor=direita,
                )

                continue

            # Operadores binários comuns associam à esquerda
            direita = self.expr_prec(prec + 1)

            esquerda = no(
                "Binario",
                op,
                operador=op.lexema,
                esquerda=esquerda,
                direita=direita,
            )

            # Em Cool, comparações não associam: a < b < c é erro
            if op.tipo in self.COMPARACOES and self.atual().tipo in self.COMPARACOES:
                self.erro("Operadores de comparação em Cool não associam")

        return esquerda

    def dispatch(self, alvo, estatico):
        if estatico:
            token = self.consumir(
                TipoToken.AT,
                "Esperado '@' no dispatch estático",
            )

            tipo_estatico = self.tipo(
                aceita_self_type=False,
                mensagem="Esperado TYPE_ID após '@' no dispatch estático",
            )

            self.consumir(
                TipoToken.DOT,
                "Esperado '.' após o tipo no dispatch estático",
            )

        else:
            token = self.consumir(
                TipoToken.DOT,
                "Esperado '.' no dispatch",
            )

            tipo_estatico = None

        metodo = self.consumir(
            TipoToken.OBJECT_ID,
            "Esperado nome do método",
        )

        self.consumir(
            TipoToken.LPAREN,
            "Esperado '(' após o nome do método",
        )

        argumentos = self.lista(TipoToken.RPAREN, self.expr)

        self.consumir(
            TipoToken.RPAREN,
            "Esperado ')' ao final da chamada",
        )

        if estatico:
            return no(
                "DispatchEstatico",
                token,
                alvo=alvo,
                tipo_estatico=tipo_estatico,
                metodo=metodo.lexema,
                argumentos=argumentos,
            )

        return no(
            "Dispatch",
            token,
            alvo=alvo,
            metodo=metodo.lexema,
            argumentos=argumentos,
        )

    def expr_prefixa(self):
        token = self.atual()

        if token.tipo == TipoToken.IF:
            return self.if_expr()

        if token.tipo == TipoToken.WHILE:
            return self.while_expr()

        if token.tipo == TipoToken.LBRACE:
            return self.bloco()

        if token.tipo == TipoToken.LET:
            return self.let_expr()

        if token.tipo == TipoToken.CASE:
            return self.case_expr()

        if token.tipo == TipoToken.NEW:
            self.avancar()

            return no(
                "New",
                token,
                tipo=self.tipo(aceita_self_type=True),
            )

        if token.tipo in self.UNARIOS:
            self.avancar()

            prec, nome_no = self.UNARIOS[token.tipo]

            return no(
                nome_no,
                token,
                expressao=self.expr_prec(prec),
            )

        if token.tipo == TipoToken.LPAREN:
            self.avancar()

            expressao = self.expr()

            self.consumir(
                TipoToken.RPAREN,
                "Esperado ')' ao final da expressão",
            )

            return expressao

        if token.tipo == TipoToken.OBJECT_ID:
            nome = self.avancar()

            # Chamada simples: metodo(...)
            if self.aceitar(TipoToken.LPAREN):
                argumentos = self.lista(TipoToken.RPAREN, self.expr)

                self.consumir(
                    TipoToken.RPAREN,
                    "Esperado ')' ao final da chamada",
                )

                return no(
                    "ChamadaSimples",
                    nome,
                    metodo=nome.lexema,
                    argumentos=argumentos,
                )

            return no(
                "Identificador",
                nome,
                nome=nome.lexema,
            )

        if token.tipo == TipoToken.SELF:
            self.avancar()

            return no(
                "Identificador",
                token,
                nome=token.lexema,
            )

        if token.tipo == TipoToken.INT_CONST:
            self.avancar()

            return no(
                "Inteiro",
                token,
                valor=int(token.lexema),
            )

        if token.tipo == TipoToken.STR_CONST:
            self.avancar()

            return no(
                "StringLiteral",
                token,
                valor=token.lexema,
            )

        if token.tipo in (TipoToken.TRUE, TipoToken.FALSE):
            self.avancar()

            return no(
                "Booleano",
                token,
                valor=token.tipo == TipoToken.TRUE,
            )

        self.erro("Esperado início válido de expressão")

    # -------------------------
    # Expressões especiais
    # -------------------------

    def if_expr(self):
        token = self.consumir(TipoToken.IF, "Esperado 'if'")

        condicao = self.expr()

        self.consumir(
            TipoToken.THEN,
            "Esperado 'then' após a condição",
        )

        entao = self.expr()

        self.consumir(
            TipoToken.ELSE,
            "Esperado 'else' no if",
        )

        senao = self.expr()

        self.consumir(
            TipoToken.FI,
            "Esperado 'fi' ao final do if",
        )

        return no(
            "If",
            token,
            condicao=condicao,
            entao=entao,
            senao=senao,
        )

    def while_expr(self):
        token = self.consumir(TipoToken.WHILE, "Esperado 'while'")

        condicao = self.expr()

        self.consumir(
            TipoToken.LOOP,
            "Esperado 'loop' no while",
        )

        corpo = self.expr()

        self.consumir(
            TipoToken.POOL,
            "Esperado 'pool' ao final do while",
        )

        return no(
            "While",
            token,
            condicao=condicao,
            corpo=corpo,
        )

    def bloco(self):
        token = self.consumir(
            TipoToken.LBRACE,
            "Esperado '{' no início do bloco",
        )

        expressoes = []

        if self.atual().tipo == TipoToken.RBRACE:
            self.erro("Bloco em Cool deve conter ao menos uma expressão")

        while self.atual().tipo != TipoToken.RBRACE:
            if self.fim():
                self.erro("Esperado '}' ao final do bloco")

            expressoes.append(self.expr())

            self.consumir(
                TipoToken.SEMICOLON,
                "Esperado ';' após a expressão do bloco",
            )

        self.consumir(
            TipoToken.RBRACE,
            "Esperado '}' ao final do bloco",
        )

        return no(
            "Bloco",
            token,
            expressoes=expressoes,
        )

    def let_expr(self):
        token = self.consumir(TipoToken.LET, "Esperado 'let'")

        declaracoes = [self.decl_let()]

        while self.aceitar(TipoToken.COMMA):
            declaracoes.append(self.decl_let())

        self.consumir(
            TipoToken.IN,
            "Esperado 'in' após as declarações do let",
        )

        return no(
            "Let",
            token,
            declaracoes=declaracoes,
            corpo=self.expr(),
        )

    def decl_let(self):
        nome = self.consumir(
            TipoToken.OBJECT_ID,
            "Esperado identificador na declaração do let",
        )

        self.consumir(
            TipoToken.COLON,
            "Esperado ':' na declaração do let",
        )

        tipo = self.tipo(aceita_self_type=True)

        inicializacao = self.expr() if self.aceitar(TipoToken.ASSIGN) else None

        return no(
            "DeclaracaoLet",
            nome,
            nome=nome.lexema,
            tipo=tipo,
            inicializacao=inicializacao,
        )

    def case_expr(self):
        token = self.consumir(TipoToken.CASE, "Esperado 'case'")

        expressao = self.expr()

        self.consumir(
            TipoToken.OF,
            "Esperado 'of' no case",
        )

        if self.atual().tipo == TipoToken.ESAC:
            self.erro("Case deve possuir ao menos um ramo")

        ramos = []

        while self.atual().tipo != TipoToken.ESAC:
            if self.fim():
                self.erro("Esperado 'esac' ao final do case")

            ramos.append(self.ramo_case())

        self.consumir(
            TipoToken.ESAC,
            "Esperado 'esac' ao final do case",
        )

        return no(
            "Case",
            token,
            expressao=expressao,
            ramos=ramos,
        )

    def ramo_case(self):
        nome = self.consumir(
            TipoToken.OBJECT_ID,
            "Esperado identificador no ramo do case",
        )

        self.consumir(
            TipoToken.COLON,
            "Esperado ':' no ramo do case",
        )

        tipo = self.tipo(
            aceita_self_type=False,
            mensagem="SELF_TYPE não pode ser usado como tipo de ramo do case",
        )

        self.consumir(
            TipoToken.DARROW,
            "Esperado '=>' no ramo do case",
        )

        expressao = self.expr()

        self.consumir(
            TipoToken.SEMICOLON,
            "Esperado ';' ao final do ramo do case",
        )

        return no(
            "RamoCase",
            nome,
            nome=nome.lexema,
            tipo=tipo,
            expressao=expressao,
        )


# =========================
# Saída JSON
# =========================


def salvar_saida(caminho_codigo, saida):
    caminho_saida = Path(caminho_codigo).with_suffix(".ast.json")

    caminho_saida.write_text(
        json.dumps(saida, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return caminho_saida


def main():
    if len(sys.argv) != 2:
        print("Uso: python parser_cool.py <arquivo.cl>")
        sys.exit(1)

    caminho_codigo = sys.argv[1]
    codigo = Path(caminho_codigo).read_text(encoding="utf-8")

    # O parser usa os tokens para verificar a estrutura sintática
    lexer = Lexer(codigo)
    tokens = lexer.tokenizar()
    erros_lexicos = getattr(lexer, "erros", [])  # lista de erros ou uma lista vazia

    # Estrutura inicial da saída:
    saida = {
        "arquivo_codigo": caminho_codigo,
        "status": "sucesso",
        "quantidade_tokens": len(tokens),
        "quantidade_erros_lexicos": len(erros_lexicos),
        "erros_lexicos": [erro.__dict__ for erro in erros_lexicos],
        "erro_sintatico": None,
        "ast": None,
    }

    if erros_lexicos:
        saida["status"] = "erro_lexico"
    else:
        try:
            parser = ParserCool(tokens)
            saida["ast"] = parser.parse()  # Início da análise sintática

        except ErroSintatico as erro:
            print(erro)
            saida["status"] = "erro_sintatico"
            saida["erro_sintatico"] = erro.para_dict()

    caminho_saida = salvar_saida(caminho_codigo, saida)

    print(f"Saída gravada em: {caminho_saida}")
    print(f"Status: {saida['status']}")

    if saida["status"] == "sucesso":
        print("Análise sintática concluída com sucesso.")

    elif saida["status"] == "erro_lexico":
        print(f"Erros léxicos: {len(erros_lexicos)}")

    else:
        print("Foi encontrado um erro sintático.")


if __name__ == "__main__":
    main()
