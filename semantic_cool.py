from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import json
import sys

from lexer_cool import Lexer
from parser_cool import ParserCool, ErroSintatico


# =========================
# Estruturas semânticas
# =========================


@dataclass
class ErroSemantico:
    mensagem: str
    linha: int
    coluna: int

    def para_dict(self):
        return {
            "mensagem": self.mensagem,
            "linha": self.linha,
            "coluna": self.coluna,
        }


@dataclass
class AtributoInfo:
    nome: str
    tipo: str
    classe: str


@dataclass
class MetodoInfo:
    nome: str
    formais: List[Tuple[str, str]]
    tipo_retorno: str
    classe: str

    def assinatura(self):
        return tuple(tipo for _, tipo in self.formais), self.tipo_retorno


@dataclass
class ClasseInfo:
    nome: str
    pai: Optional[str]
    linha: int
    coluna: int
    ast: Optional[dict] = None
    basica: bool = False
    atributos: Dict[str, AtributoInfo] = field(default_factory=dict)
    metodos: Dict[str, MetodoInfo] = field(default_factory=dict)


class Escopo:
    def __init__(self, inicial=None):
        self.pilhas = [dict(inicial or {})]

    def entrar(self):
        self.pilhas.append({})

    def sair(self):
        if len(self.pilhas) > 1:
            self.pilhas.pop()

    def declarar(self, nome, tipo):
        self.pilhas[-1][nome] = tipo

    def buscar(self, nome):
        for escopo in reversed(self.pilhas):
            if nome in escopo:
                return escopo[nome]
        return None


# =========================
# Analisador semântico
# =========================


class SemanticAnalyzer:
    PAIS_BASICOS = {
        "Object": None,
        "IO": "Object",
        "Int": "Object",
        "String": "Object",
        "Bool": "Object",
    }

    METODOS_BASICOS = {
        "Object": [
            ("abort", [], "Object"),
            ("type_name", [], "String"),
            ("copy", [], "SELF_TYPE"),
        ],
        "IO": [
            ("out_string", [("x", "String")], "SELF_TYPE"),
            ("out_int", [("x", "Int")], "SELF_TYPE"),
            ("in_string", [], "String"),
            ("in_int", [], "Int"),
        ],
        "String": [
            ("length", [], "Int"),
            ("concat", [("s", "String")], "String"),
            ("substr", [("i", "Int"), ("l", "Int")], "String"),
        ],
    }

    CLASSES_BASICAS = set(PAIS_BASICOS)
    HERANCA_PROIBIDA = {"Int", "String", "Bool"}

    def __init__(self, ast):
        self.ast = ast
        self.classes: Dict[str, ClasseInfo] = {}
        self.erros: List[ErroSemantico] = []

    # -------------------------
    # Fluxo principal
    # -------------------------

    def analisar(self):
        self.registrar_classes_basicas()
        self.coletar_classes_usuario()
        self.validar_pais()
        self.validar_ciclos_heranca()
        self.coletar_features()
        self.validar_main()
        self.checar_tipos_features()

        return {
            "quantidade_erros_semanticos": len(self.erros),
            "erros_semanticos": [erro.para_dict() for erro in self.erros],
            "tabela_classes": self.tabela_classes_para_dict(),
        }

    # -------------------------
    # Utilidades gerais
    # -------------------------

    def erro(self, mensagem, linha=1, coluna=1):
        self.erros.append(ErroSemantico(mensagem, linha, coluna))

    def tipo_existe(self, tipo: str, aceita_self_type=True):
        return (aceita_self_type and tipo == "SELF_TYPE") or tipo in self.classes

    def tipo_real(self, tipo: str, classe_atual: str):
        return classe_atual if tipo == "SELF_TYPE" else tipo

    def anotar_tipo(self, expr: dict, tipo: str):
        expr["tipo_inferido"] = tipo
        return tipo

    def exigir_tipo(self, expr: dict, obtido: str, esperado: str, contexto: str):
        if obtido != esperado:
            self.erro(
                f"{contexto}: esperado tipo '{esperado}', mas foi obtido '{obtido}'",
                expr.get("linha", 1),
                expr.get("coluna", 1),
            )

    # -------------------------
    # Classes e herança
    # -------------------------

    def registrar_classes_basicas(self):
        for nome, pai in self.PAIS_BASICOS.items():
            self.classes[nome] = ClasseInfo(
                nome=nome,
                pai=pai,
                linha=0,
                coluna=0,
                basica=True,
            )

        for classe, metodos in self.METODOS_BASICOS.items():
            for nome, formais, retorno in metodos:
                self.classes[classe].metodos[nome] = MetodoInfo(
                    nome=nome,
                    formais=formais,
                    tipo_retorno=retorno,
                    classe=classe,
                )

    def coletar_classes_usuario(self):
        for no_classe in self.ast.get("classes", []):
            nome = no_classe["nome"]

            if nome in self.CLASSES_BASICAS:
                self.erro(
                    f"A classe básica '{nome}' não pode ser redefinida",
                    no_classe["linha"],
                    no_classe["coluna"],
                )
                continue

            if nome in self.classes:
                self.erro(
                    f"A classe '{nome}' foi definida mais de uma vez",
                    no_classe["linha"],
                    no_classe["coluna"],
                )
                continue

            self.classes[nome] = ClasseInfo(
                nome=nome,
                pai=no_classe.get("pai") or "Object",
                linha=no_classe["linha"],
                coluna=no_classe["coluna"],
                ast=no_classe,
            )

    def validar_pais(self):
        for classe in self.classes.values():
            if classe.basica:
                continue

            if classe.pai in self.HERANCA_PROIBIDA:
                self.erro(
                    f"A classe '{classe.nome}' não pode herdar de '{classe.pai}'",
                    classe.linha,
                    classe.coluna,
                )

            if classe.pai not in self.classes:
                self.erro(
                    f"A classe '{classe.nome}' herda de uma classe inexistente: '{classe.pai}'",
                    classe.linha,
                    classe.coluna,
                )

    def validar_ciclos_heranca(self):
        ciclos_emitidos = set()

        for nome in self.classes:
            caminho = []
            posicao = {}
            atual = nome

            while atual in self.classes:
                if atual in posicao:
                    ciclo = caminho[posicao[atual] :] + [atual]
                    chave = tuple(ciclo)

                    if chave not in ciclos_emitidos:
                        ciclos_emitidos.add(chave)
                        classe = self.classes[atual]
                        self.erro(
                            f"Ciclo de herança detectado: {' -> '.join(ciclo)}",
                            classe.linha,
                            classe.coluna,
                        )
                    break

                posicao[atual] = len(caminho)
                caminho.append(atual)
                atual = self.classes[atual].pai

    def cadeia_heranca(self, classe_nome: str, inclui_atual=True):
        cadeia = []
        atual = self.classes.get(classe_nome)
        visitados = set()

        while atual is not None and atual.nome not in visitados:
            visitados.add(atual.nome)
            cadeia.append(atual)

            if atual.pai is None:
                break

            atual = self.classes.get(atual.pai)

        return cadeia if inclui_atual else cadeia[1:]

    def eh_subtipo(self, filho: str, ancestral: str):
        return any(classe.nome == ancestral for classe in self.cadeia_heranca(filho))

    def conforma(self, origem: str, destino: str, classe_atual: str):
        if origem == destino:
            return True

        # Uma declaração SELF_TYPE só aceita expressão cujo tipo estático também seja SELF_TYPE.
        if destino == "SELF_TYPE":
            return origem == "SELF_TYPE"

        return self.eh_subtipo(
            self.tipo_real(origem, classe_atual),
            self.tipo_real(destino, classe_atual),
        )

    def join(self, tipo_a: str, tipo_b: str, classe_atual: str):
        real_a = self.tipo_real(tipo_a, classe_atual)
        real_b = self.tipo_real(tipo_b, classe_atual)

        ancestrais_b = {classe.nome for classe in self.cadeia_heranca(real_b)}

        for classe in self.cadeia_heranca(real_a):
            if classe.nome in ancestrais_b:
                return classe.nome

        return "Object"

    # -------------------------
    # Features
    # -------------------------

    def coletar_features(self):
        for classe in self.classes.values():
            if classe.basica or classe.ast is None:
                continue

            for feature in classe.ast.get("features", []):
                if feature["no"] == "Atributo":
                    self.coletar_atributo(classe, feature)
                elif feature["no"] == "Metodo":
                    self.coletar_metodo(classe, feature)

    def coletar_atributo(self, classe: ClasseInfo, feature: dict):
        nome = feature["nome"]
        tipo = feature["tipo"]

        if nome in classe.atributos:
            self.erro(
                f"O atributo '{nome}' foi definido mais de uma vez na classe '{classe.nome}'",
                feature["linha"],
                feature["coluna"],
            )
            return

        if self.buscar_atributo_ancestral(classe.nome, nome) is not None:
            self.erro(
                f"O atributo herdado '{nome}' não pode ser redefinido na classe '{classe.nome}'",
                feature["linha"],
                feature["coluna"],
            )

        if not self.tipo_existe(tipo, aceita_self_type=True):
            self.erro(
                f"O atributo '{nome}' usa tipo inexistente: '{tipo}'",
                feature["linha"],
                feature["coluna"],
            )

        classe.atributos[nome] = AtributoInfo(nome, tipo, classe.nome)

    def coletar_metodo(self, classe: ClasseInfo, feature: dict):
        nome = feature["nome"]

        if nome in classe.metodos:
            self.erro(
                f"O método '{nome}' foi definido mais de uma vez na classe '{classe.nome}'",
                feature["linha"],
                feature["coluna"],
            )
            return

        formais = self.coletar_formais(feature)

        retorno = feature["tipo_retorno"]
        if not self.tipo_existe(retorno, aceita_self_type=True):
            self.erro(
                f"O método '{nome}' usa tipo de retorno inexistente: '{retorno}'",
                feature["linha"],
                feature["coluna"],
            )

        metodo = MetodoInfo(nome, formais, retorno, classe.nome)
        herdado = self.buscar_metodo_ancestral(classe.nome, nome)

        if herdado is not None and metodo.assinatura() != herdado.assinatura():
            self.erro(
                f"O método '{nome}' sobrescreve método herdado com assinatura incompatível",
                feature["linha"],
                feature["coluna"],
            )

        classe.metodos[nome] = metodo

    def coletar_formais(self, feature: dict):
        formais = []
        nomes = set()

        for formal in feature.get("formais", []):
            nome = formal["nome"]
            tipo = formal["tipo"]

            if nome in nomes:
                self.erro(
                    f"O parâmetro formal '{nome}' foi declarado mais de uma vez no método '{feature['nome']}'",
                    formal["linha"],
                    formal["coluna"],
                )

            nomes.add(nome)

            if not self.tipo_existe(tipo, aceita_self_type=False):
                self.erro(
                    f"O parâmetro formal '{nome}' usa tipo inexistente: '{tipo}'",
                    formal["linha"],
                    formal["coluna"],
                )

            formais.append((nome, tipo))

        return formais

    # -------------------------
    # Busca de membros
    # -------------------------

    def buscar_atributo_ancestral(self, classe_nome: str, atributo_nome: str):
        for classe in self.cadeia_heranca(classe_nome, inclui_atual=False):
            if atributo_nome in classe.atributos:
                return classe.atributos[atributo_nome]
        return None

    def buscar_metodo_ancestral(self, classe_nome: str, metodo_nome: str):
        for classe in self.cadeia_heranca(classe_nome, inclui_atual=False):
            if metodo_nome in classe.metodos:
                return classe.metodos[metodo_nome]
        return None

    def buscar_metodo(self, classe_nome: str, metodo_nome: str):
        for classe in self.cadeia_heranca(classe_nome, inclui_atual=True):
            if metodo_nome in classe.metodos:
                return classe.metodos[metodo_nome]
        return None

    def atributos_visiveis(self, classe_nome: str):
        atributos = {}

        for classe in reversed(self.cadeia_heranca(classe_nome)):
            for nome, attr in classe.atributos.items():
                atributos[nome] = attr.tipo

        return atributos

    def criar_escopo_base(self, classe_nome: str):
        escopo = self.atributos_visiveis(classe_nome)
        escopo["self"] = "SELF_TYPE"
        return Escopo(escopo)

    # -------------------------
    # Main
    # -------------------------

    def validar_main(self):
        main = self.classes.get("Main")

        if main is None:
            self.erro(
                "O programa deve possuir uma classe Main",
                self.ast.get("linha", 1),
                self.ast.get("coluna", 1),
            )
            return

        metodo = main.metodos.get("main")

        if metodo is None:
            self.erro(
                "A classe Main deve definir diretamente um método main",
                main.linha,
                main.coluna,
            )
        elif metodo.formais:
            self.erro(
                "O método Main.main não deve possuir parâmetros formais",
                main.linha,
                main.coluna,
            )

    # -------------------------
    # Checagem de tipos em features
    # -------------------------

    def checar_tipos_features(self):
        for classe in self.classes.values():
            if classe.basica or classe.ast is None:
                continue

            escopo_atributos = self.criar_escopo_base(classe.nome)

            for feature in classe.ast.get("features", []):
                if feature["no"] == "Atributo":
                    self.checar_atributo(classe, feature, escopo_atributos)
                elif feature["no"] == "Metodo":
                    self.checar_metodo(classe, feature)

    def checar_atributo(self, classe: ClasseInfo, feature: dict, escopo: Escopo):
        if feature.get("inicializacao") is None:
            return

        tipo_expr = self.inferir_tipo_expr(
            feature["inicializacao"], classe.nome, escopo
        )
        tipo_decl = feature["tipo"]

        if not self.conforma(tipo_expr, tipo_decl, classe.nome):
            self.erro(
                f"A inicialização do atributo '{feature['nome']}' possui tipo '{tipo_expr}', "
                f"mas o tipo declarado é '{tipo_decl}'",
                feature["linha"],
                feature["coluna"],
            )

    def checar_metodo(self, classe: ClasseInfo, feature: dict):
        escopo = self.criar_escopo_base(classe.nome)
        escopo.entrar()

        for formal in feature.get("formais", []):
            escopo.declarar(formal["nome"], formal["tipo"])

        tipo_corpo = self.inferir_tipo_expr(feature["corpo"], classe.nome, escopo)
        tipo_retorno = feature["tipo_retorno"]

        if not self.conforma(tipo_corpo, tipo_retorno, classe.nome):
            self.erro(
                f"O corpo do método '{feature['nome']}' possui tipo '{tipo_corpo}', "
                f"mas o retorno declarado é '{tipo_retorno}'",
                feature["linha"],
                feature["coluna"],
            )

        escopo.sair()

    # -------------------------
    # Inferência de tipos
    # -------------------------

    def inferir_tipo_expr(self, expr: dict, classe_atual: str, escopo: Escopo):
        tipo_no = expr["no"]

        if tipo_no == "Inteiro":
            return self.anotar_tipo(expr, "Int")

        if tipo_no == "StringLiteral":
            return self.anotar_tipo(expr, "String")

        if tipo_no == "Booleano":
            return self.anotar_tipo(expr, "Bool")

        if tipo_no == "Identificador":
            tipo = escopo.buscar(expr["nome"])
            if tipo is None:
                self.erro(
                    f"Identificador não declarado: '{expr['nome']}'",
                    expr["linha"],
                    expr["coluna"],
                )
                tipo = "Object"
            return self.anotar_tipo(expr, tipo)

        if tipo_no == "Atribuicao":
            return self.inferir_atribuicao(expr, classe_atual, escopo)

        if tipo_no == "Binario":
            return self.inferir_binario(expr, classe_atual, escopo)

        if tipo_no == "NegacaoAritmetica":
            tipo = self.inferir_tipo_expr(expr["expressao"], classe_atual, escopo)
            self.exigir_tipo(expr["expressao"], tipo, "Int", "Operador '~'")
            return self.anotar_tipo(expr, "Int")

        if tipo_no == "Not":
            tipo = self.inferir_tipo_expr(expr["expressao"], classe_atual, escopo)
            self.exigir_tipo(expr["expressao"], tipo, "Bool", "Operador 'not'")
            return self.anotar_tipo(expr, "Bool")

        if tipo_no == "Isvoid":
            self.inferir_tipo_expr(expr["expressao"], classe_atual, escopo)
            return self.anotar_tipo(expr, "Bool")

        if tipo_no == "New":
            if not self.tipo_existe(expr["tipo"], aceita_self_type=True):
                self.erro(
                    f"Uso de 'new' com tipo inexistente: '{expr['tipo']}'",
                    expr["linha"],
                    expr["coluna"],
                )
                return self.anotar_tipo(expr, "Object")
            return self.anotar_tipo(expr, expr["tipo"])

        if tipo_no == "If":
            return self.inferir_if(expr, classe_atual, escopo)

        if tipo_no == "While":
            return self.inferir_while(expr, classe_atual, escopo)

        if tipo_no == "Bloco":
            tipo = "Object"
            for subexpr in expr.get("expressoes", []):
                tipo = self.inferir_tipo_expr(subexpr, classe_atual, escopo)
            return self.anotar_tipo(expr, tipo)

        if tipo_no == "Let":
            return self.inferir_let(expr, classe_atual, escopo)

        if tipo_no == "Case":
            return self.inferir_case(expr, classe_atual, escopo)

        if tipo_no == "Dispatch":
            return self.inferir_dispatch(expr, classe_atual, escopo)

        if tipo_no == "DispatchEstatico":
            return self.inferir_dispatch_estatico(expr, classe_atual, escopo)

        if tipo_no == "ChamadaSimples":
            return self.inferir_chamada_simples(expr, classe_atual, escopo)

        self.erro(
            f"Nó de expressão não reconhecido na análise semântica: '{tipo_no}'",
            expr.get("linha", 1),
            expr.get("coluna", 1),
        )
        return self.anotar_tipo(expr, "Object")

    def inferir_atribuicao(self, expr: dict, classe_atual: str, escopo: Escopo):
        tipo_variavel = escopo.buscar(expr["nome"])
        tipo_valor = self.inferir_tipo_expr(expr["valor"], classe_atual, escopo)

        if tipo_variavel is None:
            self.erro(
                f"Atribuição para identificador não declarado: '{expr['nome']}'",
                expr["linha"],
                expr["coluna"],
            )
        elif not self.conforma(tipo_valor, tipo_variavel, classe_atual):
            self.erro(
                f"Atribuição inválida: expressão de tipo '{tipo_valor}' não conforma "
                f"ao tipo declarado de '{expr['nome']}', que é '{tipo_variavel}'",
                expr["linha"],
                expr["coluna"],
            )

        return self.anotar_tipo(expr, tipo_valor)

    def inferir_binario(self, expr: dict, classe_atual: str, escopo: Escopo):
        op = expr["operador"]
        esq = self.inferir_tipo_expr(expr["esquerda"], classe_atual, escopo)
        dir_ = self.inferir_tipo_expr(expr["direita"], classe_atual, escopo)

        if op in {"+", "-", "*", "/"}:
            self.exigir_tipo(expr["esquerda"], esq, "Int", f"Operador '{op}'")
            self.exigir_tipo(expr["direita"], dir_, "Int", f"Operador '{op}'")
            return self.anotar_tipo(expr, "Int")

        if op in {"<", "<="}:
            self.exigir_tipo(expr["esquerda"], esq, "Int", f"Operador '{op}'")
            self.exigir_tipo(expr["direita"], dir_, "Int", f"Operador '{op}'")
            return self.anotar_tipo(expr, "Bool")

        if op == "=":
            basicos = {"Int", "String", "Bool"}
            if (esq in basicos or dir_ in basicos) and esq != dir_:
                self.erro(
                    f"Comparação inválida com '=' entre '{esq}' e '{dir_}'",
                    expr["linha"],
                    expr["coluna"],
                )
            return self.anotar_tipo(expr, "Bool")

        self.erro(
            f"Operador binário não reconhecido: '{op}'", expr["linha"], expr["coluna"]
        )
        return self.anotar_tipo(expr, "Object")

    def inferir_if(self, expr: dict, classe_atual: str, escopo: Escopo):
        cond = self.inferir_tipo_expr(expr["condicao"], classe_atual, escopo)
        self.exigir_tipo(expr["condicao"], cond, "Bool", "Condição do if")

        tipo_then = self.inferir_tipo_expr(expr["entao"], classe_atual, escopo)
        tipo_else = self.inferir_tipo_expr(expr["senao"], classe_atual, escopo)

        return self.anotar_tipo(expr, self.join(tipo_then, tipo_else, classe_atual))

    def inferir_while(self, expr: dict, classe_atual: str, escopo: Escopo):
        cond = self.inferir_tipo_expr(expr["condicao"], classe_atual, escopo)
        self.exigir_tipo(expr["condicao"], cond, "Bool", "Condição do while")
        self.inferir_tipo_expr(expr["corpo"], classe_atual, escopo)

        return self.anotar_tipo(expr, "Object")

    def inferir_let(self, expr: dict, classe_atual: str, escopo: Escopo):
        escopo.entrar()

        for decl in expr.get("declaracoes", []):
            nome = decl["nome"]
            tipo_decl = decl["tipo"]

            if not self.tipo_existe(tipo_decl, aceita_self_type=True):
                self.erro(
                    f"Declaração let usa tipo inexistente: '{tipo_decl}'",
                    decl["linha"],
                    decl["coluna"],
                )

            if decl.get("inicializacao") is not None:
                tipo_init = self.inferir_tipo_expr(
                    decl["inicializacao"], classe_atual, escopo
                )

                if not self.conforma(tipo_init, tipo_decl, classe_atual):
                    self.erro(
                        f"Inicialização inválida no let: expressão de tipo '{tipo_init}' "
                        f"não conforma ao tipo declarado '{tipo_decl}'",
                        decl["linha"],
                        decl["coluna"],
                    )

            escopo.declarar(nome, tipo_decl)

        tipo_corpo = self.inferir_tipo_expr(expr["corpo"], classe_atual, escopo)
        escopo.sair()

        return self.anotar_tipo(expr, tipo_corpo)

    def inferir_case(self, expr: dict, classe_atual: str, escopo: Escopo):
        self.inferir_tipo_expr(expr["expressao"], classe_atual, escopo)

        tipos_declarados = set()
        tipos_resultado = []

        for ramo in expr.get("ramos", []):
            tipo_ramo = ramo["tipo"]

            if tipo_ramo in tipos_declarados:
                self.erro(
                    f"O tipo '{tipo_ramo}' aparece em mais de um ramo do case",
                    ramo["linha"],
                    ramo["coluna"],
                )

            tipos_declarados.add(tipo_ramo)

            if not self.tipo_existe(tipo_ramo, aceita_self_type=False):
                self.erro(
                    f"Ramo de case usa tipo inexistente: '{tipo_ramo}'",
                    ramo["linha"],
                    ramo["coluna"],
                )

            escopo.entrar()
            escopo.declarar(ramo["nome"], tipo_ramo)
            tipos_resultado.append(
                self.inferir_tipo_expr(ramo["expressao"], classe_atual, escopo)
            )
            escopo.sair()

        tipo_final = tipos_resultado[0] if tipos_resultado else "Object"

        for tipo in tipos_resultado[1:]:
            tipo_final = self.join(tipo_final, tipo, classe_atual)

        return self.anotar_tipo(expr, tipo_final)

    def inferir_dispatch(self, expr: dict, classe_atual: str, escopo: Escopo):
        tipo_alvo = self.inferir_tipo_expr(expr["alvo"], classe_atual, escopo)
        classe_busca = self.tipo_real(tipo_alvo, classe_atual)
        metodo = self.buscar_metodo(classe_busca, expr["metodo"])

        tipos_args = [
            self.inferir_tipo_expr(arg, classe_atual, escopo)
            for arg in expr.get("argumentos", [])
        ]

        if metodo is None:
            self.erro(
                f"Método '{expr['metodo']}' não encontrado na classe '{classe_busca}'",
                expr["linha"],
                expr["coluna"],
            )
            return self.anotar_tipo(expr, "Object")

        self.checar_argumentos(expr, metodo, tipos_args, classe_atual)

        retorno = (
            tipo_alvo if metodo.tipo_retorno == "SELF_TYPE" else metodo.tipo_retorno
        )
        return self.anotar_tipo(expr, retorno)

    def inferir_dispatch_estatico(self, expr: dict, classe_atual: str, escopo: Escopo):
        tipo_alvo = self.inferir_tipo_expr(expr["alvo"], classe_atual, escopo)
        tipo_estatico = expr["tipo_estatico"]

        tipos_args = [
            self.inferir_tipo_expr(arg, classe_atual, escopo)
            for arg in expr.get("argumentos", [])
        ]

        if not self.tipo_existe(tipo_estatico, aceita_self_type=False):
            self.erro(
                f"Dispatch estático usa tipo inexistente: '{tipo_estatico}'",
                expr["linha"],
                expr["coluna"],
            )
            return self.anotar_tipo(expr, "Object")

        if not self.conforma(tipo_alvo, tipo_estatico, classe_atual):
            self.erro(
                f"Dispatch estático inválido: tipo do alvo '{tipo_alvo}' não conforma "
                f"ao tipo declarado após '@', que é '{tipo_estatico}'",
                expr["linha"],
                expr["coluna"],
            )

        metodo = self.buscar_metodo(tipo_estatico, expr["metodo"])

        if metodo is None:
            self.erro(
                f"Método '{expr['metodo']}' não encontrado na classe '{tipo_estatico}'",
                expr["linha"],
                expr["coluna"],
            )
            return self.anotar_tipo(expr, "Object")

        self.checar_argumentos(expr, metodo, tipos_args, classe_atual)

        retorno = (
            tipo_alvo if metodo.tipo_retorno == "SELF_TYPE" else metodo.tipo_retorno
        )
        return self.anotar_tipo(expr, retorno)

    def inferir_chamada_simples(self, expr: dict, classe_atual: str, escopo: Escopo):
        metodo = self.buscar_metodo(classe_atual, expr["metodo"])

        tipos_args = [
            self.inferir_tipo_expr(arg, classe_atual, escopo)
            for arg in expr.get("argumentos", [])
        ]

        if metodo is None:
            self.erro(
                f"Método '{expr['metodo']}' não encontrado na classe '{classe_atual}'",
                expr["linha"],
                expr["coluna"],
            )
            return self.anotar_tipo(expr, "Object")

        self.checar_argumentos(expr, metodo, tipos_args, classe_atual)

        retorno = (
            "SELF_TYPE" if metodo.tipo_retorno == "SELF_TYPE" else metodo.tipo_retorno
        )
        return self.anotar_tipo(expr, retorno)

    def checar_argumentos(
        self, expr: dict, metodo: MetodoInfo, tipos_args: List[str], classe_atual: str
    ):
        if len(tipos_args) != len(metodo.formais):
            self.erro(
                f"Chamada do método '{metodo.nome}' espera {len(metodo.formais)} argumento(s), "
                f"mas recebeu {len(tipos_args)}",
                expr["linha"],
                expr["coluna"],
            )
            return

        for i, tipo_arg in enumerate(tipos_args):
            nome_formal, tipo_formal = metodo.formais[i]

            if not self.conforma(tipo_arg, tipo_formal, classe_atual):
                self.erro(
                    f"Argumento {i + 1} do método '{metodo.nome}' possui tipo '{tipo_arg}', "
                    f"mas o parâmetro '{nome_formal}' espera '{tipo_formal}'",
                    expr["linha"],
                    expr["coluna"],
                )

    # -------------------------
    # Saída
    # -------------------------

    def tabela_classes_para_dict(self):
        return {
            nome: {
                "pai": classe.pai,
                "basica": classe.basica,
                "atributos": {
                    n: {"tipo": a.tipo, "classe": a.classe}
                    for n, a in classe.atributos.items()
                },
                "metodos": {
                    n: {
                        "formais": [{"nome": fn, "tipo": ft} for fn, ft in m.formais],
                        "tipo_retorno": m.tipo_retorno,
                        "classe": m.classe,
                    }
                    for n, m in classe.metodos.items()
                },
            }
            for nome, classe in self.classes.items()
        }


# =========================
# Execução completa
# =========================


def salvar_saida(caminho_codigo, saida):
    caminho_saida = Path(caminho_codigo).with_suffix(".sem.json")
    caminho_saida.write_text(
        json.dumps(saida, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return caminho_saida


def main():
    if len(sys.argv) != 2:
        print("Uso: python semantic_cool.py <arquivo.cl>")
        sys.exit(1)

    caminho_codigo = sys.argv[1]
    codigo = Path(caminho_codigo).read_text(encoding="utf-8")

    saida = {
        "arquivo_codigo": caminho_codigo,
        "status": "sucesso",
        "quantidade_tokens": 0,
        "quantidade_erros_lexicos": 0,
        "erros_lexicos": [],
        "erro_sintatico": None,
        "quantidade_erros_semanticos": 0,
        "erros_semanticos": [],
        "tabela_classes": None,
        "ast": None,
    }

    lexer = Lexer(codigo)
    tokens = lexer.tokenizar()

    saida["quantidade_tokens"] = len(tokens)
    saida["quantidade_erros_lexicos"] = len(lexer.erros)
    saida["erros_lexicos"] = [erro.__dict__ for erro in lexer.erros]

    if lexer.erros:
        saida["status"] = "erro_lexico"

    else:
        try:
            parser = ParserCool(tokens)
            ast = parser.parse()
            saida["ast"] = ast

            analisador = SemanticAnalyzer(ast)
            resultado = analisador.analisar()

            saida["quantidade_erros_semanticos"] = resultado[
                "quantidade_erros_semanticos"
            ]
            saida["erros_semanticos"] = resultado["erros_semanticos"]
            saida["tabela_classes"] = resultado["tabela_classes"]

            if analisador.erros:
                saida["status"] = "erro_semantico"

        except ErroSintatico as erro:
            saida["status"] = "erro_sintatico"
            saida["erro_sintatico"] = erro.para_dict()

    caminho_saida = salvar_saida(caminho_codigo, saida)

    print(f"Saída semântica gravada em: {caminho_saida}")
    print(f"Status: {saida['status']}")

    if saida["status"] == "sucesso":
        print("Análise semântica concluída com sucesso.")
    elif saida["status"] == "erro_lexico":
        print(f"Erros léxicos: {saida['quantidade_erros_lexicos']}")
    elif saida["status"] == "erro_sintatico":
        print("Foi encontrado um erro sintático.")
    else:
        print(f"Erros semânticos: {saida['quantidade_erros_semanticos']}")


if __name__ == "__main__":
    main()
