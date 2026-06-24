from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import argparse
import sys

from lexer_cool import Lexer
from parser_cool import ParserCool, ErroSintatico
from semantic_cool import SemanticAnalyzer


# ============================================================
# Erros da geração de código
# ============================================================


class CodegenError(Exception):
    pass


# ============================================================
# Estruturas auxiliares
# ============================================================


@dataclass
class VarInfo:
    nome_bril: str
    tipo_bril: str
    tipo_cool: str


@dataclass
class CodeValue:
    nome_bril: str
    tipo_bril: str
    tipo_cool: str


class EscopoCodegen:
    def __init__(self):
        self.pilhas: List[Dict[str, VarInfo]] = [{}]

    def entrar(self):
        self.pilhas.append({})

    def sair(self):
        if len(self.pilhas) > 1:
            self.pilhas.pop()

    def declarar(self, nome_cool: str, nome_bril: str, tipo_bril: str, tipo_cool: str):
        self.pilhas[-1][nome_cool] = VarInfo(nome_bril, tipo_bril, tipo_cool)

    def buscar(self, nome_cool: str) -> Optional[VarInfo]:
        for escopo in reversed(self.pilhas):
            if nome_cool in escopo:
                return escopo[nome_cool]
        return None


# ============================================================
# Gerador Bril
# ============================================================


class BrilCodeGenerator:
    """
    V2 permissiva.

    Ideia principal:
    - Int  -> int
    - Bool -> bool
    - String, Object, SELF_TYPE e classes do usuário -> int

    Esse int funciona como uma referência simbólica.
    Ainda não representa objetos reais em memória.
    """

    BASIC_CLASSES = {
        "Object": None,
        "IO": "Object",
        "Int": "Object",
        "String": "Object",
        "Bool": "Object",
    }

    BASIC_METHODS = {
        "Object": {
            "abort": {
                "formais": [],
                "tipo_retorno": "Object",
            },
            "type_name": {
                "formais": [],
                "tipo_retorno": "String",
            },
            "copy": {
                "formais": [],
                "tipo_retorno": "SELF_TYPE",
            },
        },
        "IO": {
            "out_string": {
                "formais": [("x", "String")],
                "tipo_retorno": "SELF_TYPE",
            },
            "out_int": {
                "formais": [("x", "Int")],
                "tipo_retorno": "SELF_TYPE",
            },
            "in_string": {
                "formais": [],
                "tipo_retorno": "String",
            },
            "in_int": {
                "formais": [],
                "tipo_retorno": "Int",
            },
        },
        "String": {
            "length": {
                "formais": [],
                "tipo_retorno": "Int",
            },
            "concat": {
                "formais": [("s", "String")],
                "tipo_retorno": "String",
            },
            "substr": {
                "formais": [("i", "Int"), ("l", "Int")],
                "tipo_retorno": "String",
            },
        },
    }

    BINARIOS = {
        "+": ("add", "Int"),
        "-": ("sub", "Int"),
        "*": ("mul", "Int"),
        "/": ("div", "Int"),
        "<": ("lt", "Bool"),
        "<=": ("le", "Bool"),
    }

    def __init__(
        self, ast: dict, tabela_classes: Optional[dict] = None, imprimir_resultado=True
    ):
        self.ast = ast
        self.tabela_classes = tabela_classes or {}
        self.imprimir_resultado = imprimir_resultado

        self.classes = self.construir_indice_classes()

        self.funcoes_texto: List[str] = []

        self.current_class: Optional[str] = None
        self.temp = 0
        self.label = 0
        self.var_counter = 0
        self.obj_counter = 1
        self.string_counter = 1
        self.string_ids: Dict[str, int] = {}

        self.instrs: List[str] = []
        self.escopo = EscopoCodegen()

    # --------------------------------------------------------
    # Entrada principal
    # --------------------------------------------------------

    def gerar(self) -> str:
        metodos_gerados = []

        for classe_ast in self.ast.get("classes", []):
            nome_classe = classe_ast["nome"]

            for feature in classe_ast.get("features", []):
                if feature["no"] == "Metodo":
                    metodos_gerados.append(self.gerar_metodo(nome_classe, feature))

        if not metodos_gerados:
            raise CodegenError("Nenhum método de usuário encontrado para gerar Bril.")

        self.funcoes_texto.extend(metodos_gerados)
        self.funcoes_texto.append(self.gerar_wrapper_main())

        return "\n\n".join(self.funcoes_texto) + "\n"

    # --------------------------------------------------------
    # Índice de classes, atributos e métodos
    # --------------------------------------------------------

    def construir_indice_classes(self):
        classes = {}

        for nome, pai in self.BASIC_CLASSES.items():
            classes[nome] = {
                "nome": nome,
                "pai": pai,
                "basica": True,
                "ast": None,
                "atributos": [],
                "metodos": {},
            }

        for classe, metodos in self.BASIC_METHODS.items():
            for nome_metodo, info in metodos.items():
                classes[classe]["metodos"][nome_metodo] = {
                    "basico": True,
                    "classe": classe,
                    "nome": nome_metodo,
                    "formais": info["formais"],
                    "tipo_retorno": info["tipo_retorno"],
                    "ast": None,
                }

        for no_classe in self.ast.get("classes", []):
            nome = no_classe["nome"]

            classes[nome] = {
                "nome": nome,
                "pai": no_classe.get("pai") or "Object",
                "basica": False,
                "ast": no_classe,
                "atributos": [],
                "metodos": {},
            }

        for no_classe in self.ast.get("classes", []):
            nome_classe = no_classe["nome"]

            for feature in no_classe.get("features", []):
                if feature["no"] == "Atributo":
                    classes[nome_classe]["atributos"].append(feature)

                elif feature["no"] == "Metodo":
                    formais = [
                        (formal["nome"], formal["tipo"])
                        for formal in feature.get("formais", [])
                    ]

                    classes[nome_classe]["metodos"][feature["nome"]] = {
                        "basico": False,
                        "classe": nome_classe,
                        "nome": feature["nome"],
                        "formais": formais,
                        "tipo_retorno": feature["tipo_retorno"],
                        "ast": feature,
                    }

        return classes

    def cadeia_heranca(self, classe_nome: str):
        classe_nome = self.resolver_self_type(classe_nome)

        cadeia = []
        atual = classe_nome
        visitados = set()

        while atual in self.classes and atual not in visitados:
            visitados.add(atual)
            cadeia.append(self.classes[atual])
            atual = self.classes[atual].get("pai")

            if atual is None:
                break

        return cadeia

    def atributos_visiveis(self, classe_nome: str):
        atributos = []

        for classe in reversed(self.cadeia_heranca(classe_nome)):
            atributos.extend(classe.get("atributos", []))

        return atributos

    def buscar_metodo(self, classe_nome: str, metodo_nome: str):
        classe_nome = self.resolver_self_type(classe_nome)

        for classe in self.cadeia_heranca(classe_nome):
            metodos = classe.get("metodos", {})

            if metodo_nome in metodos:
                return metodos[metodo_nome]

        return None

    def nome_funcao(self, classe: str, metodo: str):
        return f"{self.sanitizar(classe)}_{self.sanitizar(metodo)}"

    # --------------------------------------------------------
    # Geração de funções
    # --------------------------------------------------------

    def gerar_metodo(self, classe_nome: str, metodo: dict) -> str:
        self.resetar_estado_funcao(classe_nome)

        nome_metodo = metodo["nome"]
        tipo_retorno_cool = metodo["tipo_retorno"]
        tipo_retorno_bril = self.tipo_bril(tipo_retorno_cool)

        args_texto = []

        nome_self = self.novo_nome_usuario("self")
        self.escopo.declarar("self", nome_self, "int", classe_nome)
        args_texto.append(f"{nome_self}: int")

        self.inicializar_atributos_visiveis(classe_nome)

        self.escopo.entrar()

        for formal in metodo.get("formais", []):
            nome_cool = formal["nome"]
            tipo_cool = formal["tipo"]
            tipo_bril = self.tipo_bril(tipo_cool)
            nome_bril = self.novo_nome_usuario(nome_cool)

            self.escopo.declarar(nome_cool, nome_bril, tipo_bril, tipo_cool)
            args_texto.append(f"{nome_bril}: {tipo_bril}")

        valor_corpo = self.gerar_expr(metodo["corpo"], valor_usado=True)
        valor_corpo = self.coagir_para_tipo_bril(valor_corpo, tipo_retorno_bril)

        self.emit(f"ret {valor_corpo.nome_bril};")

        self.escopo.sair()

        args = ", ".join(args_texto)
        nome_funcao = self.nome_funcao(classe_nome, nome_metodo)

        cabecalho = f"@{nome_funcao}({args}): {tipo_retorno_bril} {{"
        corpo = "\n".join(self.instrs)

        return f"{cabecalho}\n{corpo}\n}}"

    def gerar_wrapper_main(self) -> str:
        metodo_main = self.buscar_metodo("Main", "main")

        if metodo_main is None:
            raise CodegenError("Não foi encontrado método Main.main para gerar @main.")

        tipo_main_cool = metodo_main["tipo_retorno"]
        tipo_main_bril = self.tipo_bril(tipo_main_cool)

        linhas = [
            "@main {",
            "  __main_obj: int = const 0;",
            f"  __main_result: {tipo_main_bril} = call @Main_main __main_obj;",
        ]

        if self.imprimir_resultado and tipo_main_cool in {"Int", "Bool"}:
            linhas.append("  print __main_result;")

        linhas.append("  ret;")
        linhas.append("}")

        return "\n".join(linhas)

    def resetar_estado_funcao(self, classe_nome: str):
        self.current_class = classe_nome
        self.temp = 0
        self.label = 0
        self.var_counter = 0
        self.instrs = []
        self.escopo = EscopoCodegen()

    def inicializar_atributos_visiveis(self, classe_nome: str):
        for atributo in self.atributos_visiveis(classe_nome):
            nome_cool = atributo["nome"]
            tipo_cool = atributo["tipo"]
            tipo_bril = self.tipo_bril(tipo_cool)

            nome_bril = self.novo_nome_usuario(nome_cool)

            self.escopo.declarar(nome_cool, nome_bril, tipo_bril, tipo_cool)

            padrao = self.valor_padrao(tipo_cool)
            self.emit(f"{nome_bril}: {tipo_bril} = id {padrao.nome_bril};")

            if atributo.get("inicializacao") is not None:
                valor_init = self.gerar_expr(
                    atributo["inicializacao"],
                    valor_usado=True,
                )

                valor_init = self.coagir_para_tipo_bril(valor_init, tipo_bril)
                self.emit(f"{nome_bril}: {tipo_bril} = id {valor_init.nome_bril};")

    # --------------------------------------------------------
    # Tipos e valores padrão
    # --------------------------------------------------------

    def tipo_bril(self, tipo_cool: str) -> str:
        if tipo_cool == "Bool":
            return "bool"

        return "int"

    def resolver_self_type(self, tipo_cool: str):
        if tipo_cool == "SELF_TYPE":
            return self.current_class or "Object"

        return tipo_cool

    def valor_padrao(self, tipo_cool: str) -> CodeValue:
        tipo_real = self.resolver_self_type(tipo_cool)

        if tipo_real == "Bool":
            return self.const_bool(False)

        if tipo_real == "String":
            return self.string_const("")

        return self.const_int(0, tipo_cool=tipo_real)

    def coagir_para_tipo_bril(
        self, valor: CodeValue, tipo_bril_destino: str
    ) -> CodeValue:
        if valor.tipo_bril == tipo_bril_destino:
            return valor

        if valor.tipo_bril == "bool" and tipo_bril_destino == "int":
            return self.bool_para_int(valor)

        if valor.tipo_bril == "int" and tipo_bril_destino == "bool":
            return self.int_para_bool(valor)

        raise CodegenError(
            f"Não foi possível converter valor Bril de {valor.tipo_bril} para {tipo_bril_destino}."
        )

    def bool_para_int(self, valor: CodeValue) -> CodeValue:
        resultado = self.novo_temp()
        label_true = self.novo_label("bool_to_int_true")
        label_false = self.novo_label("bool_to_int_false")
        label_end = self.novo_label("bool_to_int_end")

        self.emit(f"br {valor.nome_bril} .{label_true} .{label_false};")

        self.emit_label(label_true)
        um = self.const_int(1)
        self.emit(f"{resultado}: int = id {um.nome_bril};")
        self.emit(f"jmp .{label_end};")

        self.emit_label(label_false)
        zero = self.const_int(0)
        self.emit(f"{resultado}: int = id {zero.nome_bril};")
        self.emit(f"jmp .{label_end};")

        self.emit_label(label_end)

        return CodeValue(resultado, "int", "Int")

    def int_para_bool(self, valor: CodeValue) -> CodeValue:
        zero = self.const_int(0)
        igual_zero = self.novo_temp()
        resultado = self.novo_temp()

        self.emit(f"{igual_zero}: bool = eq {valor.nome_bril} {zero.nome_bril};")
        self.emit(f"{resultado}: bool = not {igual_zero};")

        return CodeValue(resultado, "bool", "Bool")

    # --------------------------------------------------------
    # Utilidades de emissão
    # --------------------------------------------------------

    def emit(self, linha: str):
        self.instrs.append(f"  {linha}")

    def emit_label(self, nome: str):
        self.instrs.append(f".{nome}:")

    def novo_temp(self):
        nome = f"_t{self.temp}"
        self.temp += 1
        return nome

    def novo_label(self, prefixo: str):
        nome = f"{prefixo}_{self.label}"
        self.label += 1
        return nome

    def novo_nome_usuario(self, nome_cool: str):
        base = self.sanitizar(nome_cool)
        nome = f"{base}_{self.var_counter}"
        self.var_counter += 1
        return nome

    def sanitizar(self, nome: str):
        chars = []

        for c in str(nome):
            if c.isalnum() or c == "_":
                chars.append(c)
            else:
                chars.append("_")

        saida = "".join(chars) or "v"

        if saida[0].isdigit():
            saida = "_" + saida

        return saida

    def const_int(self, valor: int, tipo_cool="Int") -> CodeValue:
        dest = self.novo_temp()
        self.emit(f"{dest}: int = const {valor};")
        return CodeValue(dest, "int", tipo_cool)

    def const_bool(self, valor: bool) -> CodeValue:
        dest = self.novo_temp()
        texto = "true" if valor else "false"
        self.emit(f"{dest}: bool = const {texto};")
        return CodeValue(dest, "bool", "Bool")

    def string_const(self, texto: str) -> CodeValue:
        if texto not in self.string_ids:
            self.string_ids[texto] = self.string_counter
            self.string_counter += 1

        string_id = self.string_ids[texto]
        dest = self.novo_temp()
        self.emit(f"{dest}: int = const {string_id};")

        return CodeValue(dest, "int", "String")

    def novo_objeto(self, tipo_cool: str) -> CodeValue:
        tipo_real = self.resolver_self_type(tipo_cool)

        if tipo_real == "Int":
            return self.const_int(0, tipo_cool="Int")

        if tipo_real == "Bool":
            return self.const_bool(False)

        if tipo_real == "String":
            return self.string_const("")

        obj_id = self.obj_counter
        self.obj_counter += 1

        dest = self.novo_temp()
        self.emit(f"{dest}: int = const {obj_id};")

        return CodeValue(dest, "int", tipo_real)

    # --------------------------------------------------------
    # Expressões
    # --------------------------------------------------------

    def gerar_expr(self, expr: dict, valor_usado=True) -> CodeValue:
        tipo_no = expr["no"]

        if tipo_no == "Inteiro":
            return self.const_int(expr["valor"], tipo_cool="Int")

        if tipo_no == "Booleano":
            return self.const_bool(expr["valor"])

        if tipo_no == "StringLiteral":
            return self.string_const(expr["valor"])

        if tipo_no == "Identificador":
            return self.gerar_identificador(expr)

        if tipo_no == "Atribuicao":
            return self.gerar_atribuicao(expr)

        if tipo_no == "Binario":
            return self.gerar_binario(expr)

        if tipo_no == "NegacaoAritmetica":
            return self.gerar_negacao_aritmetica(expr)

        if tipo_no == "Not":
            return self.gerar_not(expr)

        if tipo_no == "Isvoid":
            return self.gerar_isvoid(expr)

        if tipo_no == "New":
            return self.gerar_new(expr)

        if tipo_no == "If":
            return self.gerar_if(expr, valor_usado=valor_usado)

        if tipo_no == "While":
            return self.gerar_while(expr, valor_usado=valor_usado)

        if tipo_no == "Bloco":
            return self.gerar_bloco(expr, valor_usado=valor_usado)

        if tipo_no == "Let":
            return self.gerar_let(expr, valor_usado=valor_usado)

        if tipo_no == "Case":
            return self.gerar_case(expr, valor_usado=valor_usado)

        if tipo_no == "ChamadaSimples":
            return self.gerar_chamada_simples(expr, valor_usado=valor_usado)

        if tipo_no == "Dispatch":
            return self.gerar_dispatch(expr, valor_usado=valor_usado)

        if tipo_no == "DispatchEstatico":
            return self.gerar_dispatch_estatico(expr, valor_usado=valor_usado)

        raise CodegenError(
            f"Nó de expressão não reconhecido na geração Bril: {tipo_no}"
        )

    def gerar_identificador(self, expr: dict) -> CodeValue:
        nome = expr["nome"]

        var = self.escopo.buscar(nome)

        if var is None:
            dummy = self.const_int(0, tipo_cool="Object")
            return dummy

        return CodeValue(var.nome_bril, var.tipo_bril, var.tipo_cool)

    def gerar_atribuicao(self, expr: dict) -> CodeValue:
        nome = expr["nome"]
        var = self.escopo.buscar(nome)

        if var is None:
            raise CodegenError(
                f"Atribuição para variável não encontrada no escopo: {nome}"
            )

        valor = self.gerar_expr(expr["valor"], valor_usado=True)
        valor = self.coagir_para_tipo_bril(valor, var.tipo_bril)

        self.emit(f"{var.nome_bril}: {var.tipo_bril} = id {valor.nome_bril};")

        return CodeValue(var.nome_bril, var.tipo_bril, var.tipo_cool)

    def gerar_binario(self, expr: dict) -> CodeValue:
        op = expr["operador"]

        esq = self.gerar_expr(expr["esquerda"], valor_usado=True)
        dir_ = self.gerar_expr(expr["direita"], valor_usado=True)

        if op in {"+", "-", "*", "/"}:
            esq = self.coagir_para_tipo_bril(esq, "int")
            dir_ = self.coagir_para_tipo_bril(dir_, "int")

            op_bril, _ = self.BINARIOS[op]
            dest = self.novo_temp()

            self.emit(f"{dest}: int = {op_bril} {esq.nome_bril} {dir_.nome_bril};")

            return CodeValue(dest, "int", "Int")

        if op in {"<", "<="}:
            esq = self.coagir_para_tipo_bril(esq, "int")
            dir_ = self.coagir_para_tipo_bril(dir_, "int")

            op_bril, _ = self.BINARIOS[op]
            dest = self.novo_temp()

            self.emit(f"{dest}: bool = {op_bril} {esq.nome_bril} {dir_.nome_bril};")

            return CodeValue(dest, "bool", "Bool")

        if op == "=":
            return self.gerar_igualdade(esq, dir_)

        raise CodegenError(f"Operador binário não suportado: {op}")

    def gerar_igualdade(self, esq: CodeValue, dir_: CodeValue) -> CodeValue:
        if esq.tipo_bril == "bool" and dir_.tipo_bril == "bool":
            not_esq = self.novo_temp()
            not_dir = self.novo_temp()
            ambos_true = self.novo_temp()
            ambos_false = self.novo_temp()
            resultado = self.novo_temp()

            self.emit(f"{not_esq}: bool = not {esq.nome_bril};")
            self.emit(f"{not_dir}: bool = not {dir_.nome_bril};")
            self.emit(f"{ambos_true}: bool = and {esq.nome_bril} {dir_.nome_bril};")
            self.emit(f"{ambos_false}: bool = and {not_esq} {not_dir};")
            self.emit(f"{resultado}: bool = or {ambos_true} {ambos_false};")

            return CodeValue(resultado, "bool", "Bool")

        esq = self.coagir_para_tipo_bril(esq, "int")
        dir_ = self.coagir_para_tipo_bril(dir_, "int")

        dest = self.novo_temp()
        self.emit(f"{dest}: bool = eq {esq.nome_bril} {dir_.nome_bril};")

        return CodeValue(dest, "bool", "Bool")

    def gerar_negacao_aritmetica(self, expr: dict) -> CodeValue:
        valor = self.gerar_expr(expr["expressao"], valor_usado=True)
        valor = self.coagir_para_tipo_bril(valor, "int")

        zero = self.const_int(0)
        dest = self.novo_temp()

        self.emit(f"{dest}: int = sub {zero.nome_bril} {valor.nome_bril};")

        return CodeValue(dest, "int", "Int")

    def gerar_not(self, expr: dict) -> CodeValue:
        valor = self.gerar_expr(expr["expressao"], valor_usado=True)
        valor = self.coagir_para_tipo_bril(valor, "bool")

        dest = self.novo_temp()
        self.emit(f"{dest}: bool = not {valor.nome_bril};")

        return CodeValue(dest, "bool", "Bool")

    def gerar_isvoid(self, expr: dict) -> CodeValue:
        self.gerar_expr(expr["expressao"], valor_usado=True)
        return self.const_bool(False)

    def gerar_new(self, expr: dict) -> CodeValue:
        return self.novo_objeto(expr["tipo"])

    def gerar_if(self, expr: dict, valor_usado=True) -> CodeValue:
        cond = self.gerar_expr(expr["condicao"], valor_usado=True)
        cond = self.coagir_para_tipo_bril(cond, "bool")

        label_then = self.novo_label("if_then")
        label_else = self.novo_label("if_else")
        label_fim = self.novo_label("if_end")

        tipo_resultado_bril = self.planejar_tipo_bril(expr)
        resultado = self.novo_temp()

        self.emit(f"br {cond.nome_bril} .{label_then} .{label_else};")

        self.emit_label(label_then)
        valor_then = self.gerar_expr(expr["entao"], valor_usado=valor_usado)

        if valor_usado:
            valor_then = self.coagir_para_tipo_bril(valor_then, tipo_resultado_bril)
            self.emit(
                f"{resultado}: {tipo_resultado_bril} = id {valor_then.nome_bril};"
            )

        self.emit(f"jmp .{label_fim};")

        self.emit_label(label_else)
        valor_else = self.gerar_expr(expr["senao"], valor_usado=valor_usado)

        if valor_usado:
            valor_else = self.coagir_para_tipo_bril(valor_else, tipo_resultado_bril)
            self.emit(
                f"{resultado}: {tipo_resultado_bril} = id {valor_else.nome_bril};"
            )

        self.emit(f"jmp .{label_fim};")

        self.emit_label(label_fim)

        if valor_usado:
            tipo_cool = expr.get("tipo_inferido") or (
                "Bool" if tipo_resultado_bril == "bool" else "Object"
            )
            return CodeValue(resultado, tipo_resultado_bril, tipo_cool)

        return self.const_int(0, tipo_cool="Object")

    def gerar_while(self, expr: dict, valor_usado=True) -> CodeValue:
        label_cond = self.novo_label("while_cond")
        label_body = self.novo_label("while_body")
        label_fim = self.novo_label("while_end")

        self.emit(f"jmp .{label_cond};")

        self.emit_label(label_cond)
        cond = self.gerar_expr(expr["condicao"], valor_usado=True)
        cond = self.coagir_para_tipo_bril(cond, "bool")

        self.emit(f"br {cond.nome_bril} .{label_body} .{label_fim};")

        self.emit_label(label_body)
        self.gerar_expr(expr["corpo"], valor_usado=False)
        self.emit(f"jmp .{label_cond};")

        self.emit_label(label_fim)

        return self.const_int(0, tipo_cool="Object")

    def gerar_bloco(self, expr: dict, valor_usado=True) -> CodeValue:
        expressoes = expr.get("expressoes", [])

        if not expressoes:
            return self.const_int(0, tipo_cool="Object")

        for subexpr in expressoes[:-1]:
            self.gerar_expr(subexpr, valor_usado=False)

        return self.gerar_expr(expressoes[-1], valor_usado=valor_usado)

    def gerar_let(self, expr: dict, valor_usado=True) -> CodeValue:
        self.escopo.entrar()

        for decl in expr.get("declaracoes", []):
            nome_cool = decl["nome"]
            tipo_cool = decl["tipo"]
            tipo_bril = self.tipo_bril(tipo_cool)
            nome_bril = self.novo_nome_usuario(nome_cool)

            if decl.get("inicializacao") is not None:
                valor_init = self.gerar_expr(
                    decl["inicializacao"],
                    valor_usado=True,
                )
            else:
                valor_init = self.valor_padrao(tipo_cool)

            valor_init = self.coagir_para_tipo_bril(valor_init, tipo_bril)

            self.escopo.declarar(nome_cool, nome_bril, tipo_bril, tipo_cool)
            self.emit(f"{nome_bril}: {tipo_bril} = id {valor_init.nome_bril};")

        valor_corpo = self.gerar_expr(expr["corpo"], valor_usado=valor_usado)

        self.escopo.sair()

        return valor_corpo

    def gerar_case(self, expr: dict, valor_usado=True) -> CodeValue:
        valor_base = self.gerar_expr(expr["expressao"], valor_usado=True)
        ramos = expr.get("ramos", [])

        if not ramos:
            return self.const_int(0, tipo_cool="Object")

        # Simplificação V2:
        # escolhe estruturalmente o primeiro ramo.
        ramo = ramos[0]

        self.escopo.entrar()

        tipo_ramo = ramo["tipo"]
        tipo_bril = self.tipo_bril(tipo_ramo)
        nome_bril = self.novo_nome_usuario(ramo["nome"])

        valor_base = self.coagir_para_tipo_bril(valor_base, tipo_bril)

        self.escopo.declarar(ramo["nome"], nome_bril, tipo_bril, tipo_ramo)
        self.emit(f"{nome_bril}: {tipo_bril} = id {valor_base.nome_bril};")

        resultado = self.gerar_expr(ramo["expressao"], valor_usado=valor_usado)

        self.escopo.sair()

        return resultado

    # --------------------------------------------------------
    # Chamadas e dispatch
    # --------------------------------------------------------

    def gerar_chamada_simples(self, expr: dict, valor_usado=True) -> CodeValue:
        self_var = self.escopo.buscar("self")

        if self_var is None:
            receiver = self.const_int(0, tipo_cool=self.current_class or "Object")
        else:
            receiver = CodeValue(
                self_var.nome_bril,
                self_var.tipo_bril,
                self_var.tipo_cool,
            )

        return self.gerar_chamada_metodo(
            receiver=receiver,
            metodo_nome=expr["metodo"],
            argumentos_expr=expr.get("argumentos", []),
            classe_busca=receiver.tipo_cool,
            valor_usado=valor_usado,
        )

    def gerar_dispatch(self, expr: dict, valor_usado=True) -> CodeValue:
        receiver = self.gerar_expr(expr["alvo"], valor_usado=True)

        return self.gerar_chamada_metodo(
            receiver=receiver,
            metodo_nome=expr["metodo"],
            argumentos_expr=expr.get("argumentos", []),
            classe_busca=receiver.tipo_cool,
            valor_usado=valor_usado,
        )

    def gerar_dispatch_estatico(self, expr: dict, valor_usado=True) -> CodeValue:
        receiver = self.gerar_expr(expr["alvo"], valor_usado=True)

        return self.gerar_chamada_metodo(
            receiver=receiver,
            metodo_nome=expr["metodo"],
            argumentos_expr=expr.get("argumentos", []),
            classe_busca=expr["tipo_estatico"],
            valor_usado=valor_usado,
        )

    def gerar_chamada_metodo(
        self,
        receiver: CodeValue,
        metodo_nome: str,
        argumentos_expr: List[dict],
        classe_busca: str,
        valor_usado=True,
    ) -> CodeValue:
        classe_busca = self.resolver_self_type(classe_busca)
        metodo = self.buscar_metodo(classe_busca, metodo_nome)

        if metodo is None:
            # Fallback permissivo:
            # não quebra a geração para chamadas que a V2 ainda não consegue resolver.
            self.emit("nop;")
            return self.const_int(0, tipo_cool="Object")

        if metodo["basico"]:
            return self.gerar_chamada_basica(
                receiver=receiver,
                metodo=metodo,
                argumentos_expr=argumentos_expr,
                valor_usado=valor_usado,
            )

        args_bril = []

        formais = metodo.get("formais", [])

        for i, arg_expr in enumerate(argumentos_expr):
            valor_arg = self.gerar_expr(arg_expr, valor_usado=True)

            if i < len(formais):
                _, tipo_formal_cool = formais[i]
                tipo_formal_bril = self.tipo_bril(tipo_formal_cool)
                valor_arg = self.coagir_para_tipo_bril(valor_arg, tipo_formal_bril)

            args_bril.append(valor_arg.nome_bril)

        tipo_retorno_cool = metodo["tipo_retorno"]

        if tipo_retorno_cool == "SELF_TYPE":
            tipo_retorno_cool_real = receiver.tipo_cool
        else:
            tipo_retorno_cool_real = tipo_retorno_cool

        tipo_retorno_bril = self.tipo_bril(tipo_retorno_cool_real)
        dest = self.novo_temp()

        nome_funcao = self.nome_funcao(metodo["classe"], metodo["nome"])
        todos_args = [receiver.nome_bril] + args_bril
        sufixo_args = " " + " ".join(todos_args) if todos_args else ""

        self.emit(f"{dest}: {tipo_retorno_bril} = call @{nome_funcao}{sufixo_args};")

        return CodeValue(dest, tipo_retorno_bril, tipo_retorno_cool_real)

    def gerar_chamada_basica(
        self,
        receiver: CodeValue,
        metodo: dict,
        argumentos_expr: List[dict],
        valor_usado=True,
    ) -> CodeValue:
        nome = metodo["nome"]

        if nome == "abort":
            self.emit("nop;")
            return self.const_int(0, tipo_cool="Object")

        if nome == "type_name":
            return self.string_const(receiver.tipo_cool)

        if nome == "copy":
            return receiver

        if nome == "out_int":
            if argumentos_expr:
                arg = self.gerar_expr(argumentos_expr[0], valor_usado=True)
                arg = self.coagir_para_tipo_bril(arg, "int")
                self.emit(f"print {arg.nome_bril};")

            return CodeValue(receiver.nome_bril, receiver.tipo_bril, receiver.tipo_cool)

        if nome == "out_string":
            if argumentos_expr:
                arg = self.gerar_expr(argumentos_expr[0], valor_usado=True)
                arg = self.coagir_para_tipo_bril(arg, "int")

                # Bril core não possui String.
                # Nesta V2, cada string COOL é representada por um ID inteiro.
                # Então out_string imprime o ID da string.
                self.emit(f"print {arg.nome_bril};")
            else:
                self.emit("nop;")

            return CodeValue(receiver.nome_bril, receiver.tipo_bril, receiver.tipo_cool)

        if nome == "in_int":
            return self.const_int(0, tipo_cool="Int")

        if nome == "in_string":
            return self.string_const("")

        if nome == "length":
            # Como String ainda é simbólica, usamos tamanho 0 provisoriamente.
            return self.const_int(0, tipo_cool="Int")

        if nome == "concat":
            for arg_expr in argumentos_expr:
                self.gerar_expr(arg_expr, valor_usado=True)

            # Concatenação ainda não é real.
            # Retorna uma string simbólica.
            return self.string_const("<concat>")

        if nome == "substr":
            for arg_expr in argumentos_expr:
                self.gerar_expr(arg_expr, valor_usado=True)

            # Substring ainda não é real.
            # Retorna uma string simbólica.
            return self.string_const("<substr>")

        self.emit("nop;")
        return self.const_int(0, tipo_cool="Object")

    # --------------------------------------------------------
    # Planejamento simples de tipo Bril para if
    # --------------------------------------------------------

    def planejar_tipo_bril(self, expr: dict) -> str:
        tipo_inferido = expr.get("tipo_inferido")

        if tipo_inferido is not None:
            return self.tipo_bril(tipo_inferido)

        tipo_no = expr.get("no")

        if tipo_no == "Booleano":
            return "bool"

        if tipo_no == "Inteiro":
            return "int"

        if tipo_no == "StringLiteral":
            return "int"

        if tipo_no == "Identificador":
            var = self.escopo.buscar(expr["nome"])
            return var.tipo_bril if var else "int"

        if tipo_no == "Binario":
            op = expr["operador"]
            return "bool" if op in {"<", "<=", "="} else "int"

        if tipo_no in {"Not", "Isvoid"}:
            return "bool"

        if tipo_no == "NegacaoAritmetica":
            return "int"

        if tipo_no == "If":
            tipo_then = self.planejar_tipo_bril(expr["entao"])
            tipo_else = self.planejar_tipo_bril(expr["senao"])
            return tipo_then if tipo_then == tipo_else else "int"

        if tipo_no == "While":
            return "int"

        if tipo_no == "Bloco":
            expressoes = expr.get("expressoes", [])
            return self.planejar_tipo_bril(expressoes[-1]) if expressoes else "int"

        if tipo_no == "Let":
            return self.planejar_tipo_bril(expr["corpo"])

        if tipo_no == "ChamadaSimples":
            self_var = self.escopo.buscar("self")
            classe = self_var.tipo_cool if self_var else self.current_class
            metodo = self.buscar_metodo(classe or "Object", expr["metodo"])

            if metodo:
                return self.tipo_bril(metodo["tipo_retorno"])

            return "int"

        if tipo_no in {"Dispatch", "DispatchEstatico"}:
            return "int"

        if tipo_no in {"New", "Case"}:
            return "int"

        return "int"


# ============================================================
# Pipeline completo: COOL -> Lexer -> Parser -> Semântico -> Bril
# ============================================================


def compilar_para_bril(
    caminho_codigo: str,
    imprimir_resultado=True,
    strict_semantic=False,
) -> Path:
    caminho = Path(caminho_codigo)
    codigo = caminho.read_text(encoding="utf-8")

    lexer = Lexer(codigo)
    tokens = lexer.tokenizar()

    if lexer.erros:
        mensagens = "\n".join(
            f"- linha {e.linha}, coluna {e.coluna}: {e.mensagem}" for e in lexer.erros
        )
        raise CodegenError(f"Erros léxicos impedem a geração Bril:\n{mensagens}")

    try:
        parser = ParserCool(tokens)
        ast = parser.parse()
    except ErroSintatico as erro:
        raise CodegenError(f"Erro sintático impede a geração Bril:\n{erro}")

    tabela_classes = {}

    try:
        analisador = SemanticAnalyzer(ast)
        resultado_semantico = analisador.analisar()
        tabela_classes = resultado_semantico.get("tabela_classes", {})

        if analisador.erros:
            mensagens = "\n".join(
                f"- linha {e.linha}, coluna {e.coluna}: {e.mensagem}"
                for e in analisador.erros
            )

            if strict_semantic:
                raise CodegenError(
                    f"Erros semânticos impedem a geração Bril:\n{mensagens}"
                )

            print(
                "Aviso: a análise semântica encontrou erro(s), "
                "mas a geração Bril continuará em modo permissivo.",
                file=sys.stderr,
            )
            print(mensagens, file=sys.stderr)

    except CodegenError:
        raise

    except Exception as erro:
        if strict_semantic:
            raise CodegenError(
                f"A análise semântica falhou e o modo estrito está ativo:\n{erro}"
            )

        print(
            "Aviso: a análise semântica falhou, "
            "mas a geração Bril continuará em modo permissivo.",
            file=sys.stderr,
        )
        print(str(erro), file=sys.stderr)

    gerador = BrilCodeGenerator(
        ast=ast,
        tabela_classes=tabela_classes,
        imprimir_resultado=imprimir_resultado,
    )

    codigo_bril = gerador.gerar()

    caminho_saida = caminho.with_suffix(".bril")
    caminho_saida.write_text(codigo_bril, encoding="utf-8")

    return caminho_saida


def main():
    argparser = argparse.ArgumentParser(
        description="Gera código Bril textual a partir de um programa COOL."
    )

    argparser.add_argument(
        "arquivo",
        help="Arquivo .cl de entrada.",
    )

    argparser.add_argument(
        "--no-print-main",
        action="store_true",
        help="Não imprime automaticamente o resultado de Main.main no @main Bril.",
    )

    argparser.add_argument(
        "--strict-semantic",
        action="store_true",
        help="Interrompe a geração caso a análise semântica encontre erro.",
    )

    args = argparser.parse_args()

    try:
        caminho_saida = compilar_para_bril(
            args.arquivo,
            imprimir_resultado=not args.no_print_main,
            strict_semantic=args.strict_semantic,
        )

        print(f"Código Bril gerado em: {caminho_saida}")

    except CodegenError as erro:
        print("Erro na geração de código Bril:")
        print(erro)
        sys.exit(1)


if __name__ == "__main__":
    main()
