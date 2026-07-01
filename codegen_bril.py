from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set
import argparse
import json
import sys

from lexer_cool import Lexer
from parser_cool import ParserCool, ErroSintatico
from semantic_cool import SemanticAnalyzer


# ============================================================
# Tipos Bril usados no JSON
# ============================================================

BRIL_INT = "int"
BRIL_BOOL = "bool"
BRIL_CHAR = "char"
BRIL_ANY = "any"
PTR_ANY = {"ptr": "any"}
PTR_CHAR = {"ptr": "char"}


def same_type(a: Any, b: Any) -> bool:
    return a == b


def type_to_text(t: Any) -> str:
    if isinstance(t, dict) and "ptr" in t:
        return f"ptr<{type_to_text(t['ptr'])}>"
    return str(t)


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
    tipo_bril: Any
    tipo_cool: str


@dataclass
class CodeValue:
    nome_bril: str
    tipo_bril: Any
    tipo_cool: str


class EscopoCodegen:
    def __init__(self):
        self.pilhas: List[Dict[str, VarInfo]] = [{}]

    def entrar(self):
        self.pilhas.append({})

    def sair(self):
        if len(self.pilhas) > 1:
            self.pilhas.pop()

    def declarar(self, nome_cool: str, nome_bril: str, tipo_bril: Any, tipo_cool: str):
        self.pilhas[-1][nome_cool] = VarInfo(nome_bril, tipo_bril, tipo_cool)

    def buscar(self, nome_cool: str) -> Optional[VarInfo]:
        for escopo in reversed(self.pilhas):
            if nome_cool in escopo:
                return escopo[nome_cool]
        return None


# ============================================================
# Gerador Bril V4.1
# ============================================================


class BrilCodeGenerator:
    """
    V4.1 experimental com saída JSON canônica.

    Principais decisões:
    - Int       -> int
    - Bool      -> bool
    - String    -> ptr<char>, terminada por caractere NUL ('\\0')
    - Object    -> ptr<any>
    - SELF_TYPE -> ptr<any>
    - Classes de usuário -> ptr<any>

    Objetos COOL de usuário são regiões ptr<any>:
    - slot 0: tag dinâmica da classe
    - slot 1..n: atributos visíveis, incluindo herdados

    Strings COOL são regiões ptr<char>:
    - chars Unicode em sequência
    - último char é NUL ('\\0')

    Limitações honestas:
    - Bril não tem saída de string contínua como COOL; print de char depende do brili.
    - Int/Bool continuam unboxed.
    - Object para Int/Bool/String ainda exige aproximações em alguns casos.
    - abort() não encerra de verdade.
    - igualdade de objetos usa tag dinâmica como aproximação, não endereço real.
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
            "abort": {"formais": [], "tipo_retorno": "Object"},
            "type_name": {"formais": [], "tipo_retorno": "String"},
            "copy": {"formais": [], "tipo_retorno": "SELF_TYPE"},
        },
        "IO": {
            "out_string": {"formais": [("x", "String")], "tipo_retorno": "SELF_TYPE"},
            "out_int": {"formais": [("x", "Int")], "tipo_retorno": "SELF_TYPE"},
            "in_string": {"formais": [], "tipo_retorno": "String"},
            "in_int": {"formais": [], "tipo_retorno": "Int"},
        },
        "String": {
            "length": {"formais": [], "tipo_retorno": "Int"},
            "concat": {"formais": [("s", "String")], "tipo_retorno": "String"},
            "substr": {
                "formais": [("i", "Int"), ("l", "Int")],
                "tipo_retorno": "String",
            },
        },
    }

    BASIC_VALUE_TYPES = {"Int", "Bool", "String"}

    def __init__(
        self,
        ast: dict,
        tabela_classes: Optional[dict] = None,
        imprimir_resultado: bool = True,
        debug_string_ids: bool = False,
        usar_main_input: bool = False,
        main_int_inputs_count: Optional[int] = None,
        main_string_inputs: Optional[List[str]] = None,
    ):
        self.ast = ast
        self.tabela_classes = tabela_classes or {}
        self.imprimir_resultado = imprimir_resultado
        self.debug_string_ids = debug_string_ids
        self.usar_main_input = usar_main_input
        self.main_int_inputs_count_override = main_int_inputs_count
        self.main_string_inputs = list(main_string_inputs or [])

        self.classes = self.construir_indice_classes()
        self.class_tags = self.construir_tags_classes()
        self.class_children = self.construir_filhos_classes()
        self.attr_layouts = self.construir_layouts_atributos()
        self.class_sizes = self.construir_tamanhos_classes()
        self.wrapper_methods = self.construir_wrapper_methods()

        # Entrada simulada para métodos IO.in_int()/IO.in_string().
        # Bril padrão não tem leitura interativa; argumentos de @main são o caminho
        # mais compatível. Quando --main-input está ativo, contamos chamadas in_int
        # dentro de Main.main e criamos __input0, __input1, ... em @main.
        if self.usar_main_input:
            if self.main_int_inputs_count_override is not None:
                self.main_int_input_count = max(
                    0, int(self.main_int_inputs_count_override)
                )
            else:
                self.main_int_input_count = self.contar_in_int_em_main()
        else:
            self.main_int_input_count = 0
        self.input_int_counter = 0
        self.input_string_counter = 0

        self.functions: List[dict] = []
        self.current_class: Optional[str] = None
        self.temp = 0
        self.label = 0
        self.var_counter = 0
        self.instrs: List[dict] = []
        self.escopo = EscopoCodegen()
        self.string_counter = 1
        self.string_ids: Dict[str, int] = {}

    # ========================================================
    # Pré-análise simples de entradas de usuário
    # ========================================================

    def obter_main_main_ast(self) -> Optional[dict]:
        for classe_ast in self.ast.get("classes", []):
            if classe_ast.get("nome") != "Main":
                continue
            for feature in classe_ast.get("features", []):
                if feature.get("no") == "Metodo" and feature.get("nome") == "main":
                    return feature
        return None

    def contar_chamadas_io(self, node: Any, metodo_nome: str) -> int:
        if isinstance(node, dict):
            total = 0
            if node.get("no") == "ChamadaSimples" and node.get("metodo") == metodo_nome:
                total += 1
            if (
                node.get("no") in {"Dispatch", "DispatchEstatico"}
                and node.get("metodo") == metodo_nome
            ):
                total += 1
            for valor in node.values():
                total += self.contar_chamadas_io(valor, metodo_nome)
            return total
        if isinstance(node, list):
            return sum(self.contar_chamadas_io(item, metodo_nome) for item in node)
        return 0

    def contar_in_int_em_main(self) -> int:
        metodo_main = self.obter_main_main_ast()
        if metodo_main is None:
            return 0
        return self.contar_chamadas_io(metodo_main.get("corpo"), "in_int")

    # ========================================================
    # Entrada principal
    # ========================================================

    def gerar(self) -> dict:
        funcs = []

        for classe_ast in self.ast.get("classes", []):
            nome_classe = classe_ast["nome"]
            for feature in classe_ast.get("features", []):
                if feature["no"] == "Metodo":
                    funcs.append(self.gerar_metodo(nome_classe, feature))

        for classe_nome, metodo_nome in sorted(self.wrapper_methods):
            funcs.append(self.gerar_wrapper_dispatch(classe_nome, metodo_nome))

        if not funcs:
            raise CodegenError("Nenhum método de usuário encontrado para gerar Bril.")

        funcs.append(self.gerar_funcao_new_main())
        funcs.append(self.gerar_wrapper_main())

        return {"functions": funcs}

    # ========================================================
    # Índices de classe, atributos, herança e métodos
    # ========================================================

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

    def construir_tags_classes(self):
        # Tags só para objetos ptr<any>. String usa ptr<char> e não precisa de tag.
        tags = {}
        tag = 1
        for nome, info in self.classes.items():
            if info.get("basica"):
                continue
            tags[nome] = tag
            tag += 1
        return tags

    def construir_filhos_classes(self):
        filhos = {nome: [] for nome in self.classes}
        for nome, info in self.classes.items():
            pai = info.get("pai")
            if pai in filhos:
                filhos[pai].append(nome)
        return filhos

    def cadeia_heranca_nomes(self, classe_nome: str):
        classe_nome = self.resolver_self_type(classe_nome)
        cadeia = []
        atual = classe_nome
        visitados = set()
        while atual in self.classes and atual not in visitados:
            visitados.add(atual)
            cadeia.append(atual)
            atual = self.classes[atual].get("pai")
            if atual is None:
                break
        return cadeia

    def atributos_visiveis(self, classe_nome: str):
        atributos = []
        for nome_classe in reversed(self.cadeia_heranca_nomes(classe_nome)):
            classe = self.classes.get(nome_classe)
            if not classe:
                continue
            for atributo in classe.get("atributos", []):
                if not any(a["nome"] == atributo["nome"] for a in atributos):
                    atributos.append(atributo)
        return atributos

    def construir_layouts_atributos(self):
        layouts = {}
        for nome_classe, info_classe in self.classes.items():
            if info_classe.get("basica"):
                continue
            layout = {}
            offset = 1
            for atributo in self.atributos_visiveis(nome_classe):
                nome_attr = atributo["nome"]
                if nome_attr not in layout:
                    layout[nome_attr] = {
                        "offset": offset,
                        "tipo_cool": atributo["tipo"],
                        "tipo_bril": self.tipo_bril(atributo["tipo"]),
                        "ast": atributo,
                    }
                    offset += 1
            layouts[nome_classe] = layout
        return layouts

    def construir_tamanhos_classes(self):
        return {
            nome_classe: len(layout) + 1
            for nome_classe, layout in self.attr_layouts.items()
        }

    def classe_definidora_metodo(self, classe_nome: str, metodo_nome: str):
        classe_nome = self.resolver_self_type(classe_nome)
        atual = classe_nome
        visitados = set()
        while atual in self.classes and atual not in visitados:
            visitados.add(atual)
            metodos = self.classes[atual].get("metodos", {})
            if metodo_nome in metodos:
                return atual
            atual = self.classes[atual].get("pai")
            if atual is None:
                break
        return None

    def buscar_metodo(self, classe_nome: str, metodo_nome: str):
        definidora = self.classe_definidora_metodo(classe_nome, metodo_nome)
        if definidora is None:
            return None
        return self.classes[definidora]["metodos"][metodo_nome]

    def descendentes_incluindo_classe(self, classe_nome: str):
        resultado = []
        if classe_nome in self.classes and not self.classes[classe_nome].get("basica"):
            resultado.append(classe_nome)
        for filho in self.class_children.get(classe_nome, []):
            if self.classes.get(filho, {}).get("basica"):
                continue
            resultado.extend(self.descendentes_incluindo_classe(filho))
        return resultado

    def distancia_ate_ancestral(
        self, classe_nome: str, ancestral: str
    ) -> Optional[int]:
        atual = classe_nome
        dist = 0
        visitados = set()
        while atual in self.classes and atual not in visitados:
            if atual == ancestral:
                return dist
            visitados.add(atual)
            atual = self.classes[atual].get("pai")
            dist += 1
            if atual is None:
                break
        return None

    def construir_wrapper_methods(self) -> Set[Tuple[str, str]]:
        wrappers = set()
        for classe_nome, info_classe in self.classes.items():
            if info_classe.get("basica"):
                continue
            for metodo_nome in info_classe.get("metodos", {}):
                for descendente in self.descendentes_incluindo_classe(classe_nome):
                    if descendente == classe_nome:
                        continue
                    definidora = self.classe_definidora_metodo(descendente, metodo_nome)
                    if definidora is not None and definidora != classe_nome:
                        wrappers.add((classe_nome, metodo_nome))
                        break
        return wrappers

    def metodo_tem_wrapper(self, classe_nome: str, metodo_nome: str):
        return (classe_nome, metodo_nome) in self.wrapper_methods

    def nome_funcao(self, classe: str, metodo: str):
        return f"{self.sanitizar(classe)}_{self.sanitizar(metodo)}"

    def nome_funcao_impl(self, classe: str, metodo: str):
        base = self.nome_funcao(classe, metodo)
        if self.metodo_tem_wrapper(classe, metodo):
            return f"{base}__impl"
        return base

    def nome_funcao_chamada(self, classe: str, metodo: str):
        return self.nome_funcao(classe, metodo)

    # ========================================================
    # Emissão JSON Bril
    # ========================================================

    def emit(self, instr: dict):
        self.instrs.append(instr)

    def emit_label(self, nome: str):
        self.emit({"label": nome})

    def emit_const(self, dest: str, typ: Any, value: Any):
        self.emit({"op": "const", "dest": dest, "type": typ, "value": value})

    def emit_value(
        self,
        op: str,
        dest: str,
        typ: Any,
        args: Optional[List[str]] = None,
        funcs: Optional[List[str]] = None,
        labels: Optional[List[str]] = None,
    ):
        instr: Dict[str, Any] = {"op": op, "dest": dest, "type": typ}
        if args:
            instr["args"] = args
        if funcs:
            instr["funcs"] = funcs
        if labels:
            instr["labels"] = labels
        self.emit(instr)

    def emit_effect(
        self,
        op: str,
        args: Optional[List[str]] = None,
        funcs: Optional[List[str]] = None,
        labels: Optional[List[str]] = None,
    ):
        instr: Dict[str, Any] = {"op": op}
        if args:
            instr["args"] = args
        if funcs:
            instr["funcs"] = funcs
        if labels:
            instr["labels"] = labels
        self.emit(instr)

    def novo_temp(self):
        nome = f"_t{self.temp}"
        self.temp += 1
        return nome

    def novo_label(self, prefixo: str):
        nome = f"{self.sanitizar(prefixo)}_{self.label}"
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

    def resetar_estado_funcao(self, classe_nome: str):
        self.current_class = classe_nome
        self.temp = 0
        self.label = 0
        self.var_counter = 0
        self.instrs = []
        self.escopo = EscopoCodegen()
        self.input_int_counter = 0
        self.input_string_counter = 0

    # ========================================================
    # Tipos, coerções e constantes
    # ========================================================

    def tipo_bril(self, tipo_cool: str) -> Any:
        tipo_real = self.resolver_self_type(tipo_cool)
        if tipo_real == "Bool":
            return BRIL_BOOL
        if tipo_real == "Int":
            return BRIL_INT
        if tipo_real == "String":
            return PTR_CHAR
        return PTR_ANY

    def resolver_self_type(self, tipo_cool: str):
        if tipo_cool == "SELF_TYPE":
            return self.current_class or "Object"
        return tipo_cool

    def const_int(self, valor: int, tipo_cool="Int") -> CodeValue:
        dest = self.novo_temp()
        self.emit_const(dest, BRIL_INT, int(valor))
        return CodeValue(dest, BRIL_INT, tipo_cool)

    def const_bool(self, valor: bool) -> CodeValue:
        dest = self.novo_temp()
        self.emit_const(dest, BRIL_BOOL, bool(valor))
        return CodeValue(dest, BRIL_BOOL, "Bool")

    def const_char(self, valor: str) -> CodeValue:
        if len(valor) != 1:
            raise CodegenError(f"Constante char inválida: {valor!r}")
        dest = self.novo_temp()
        self.emit_const(dest, BRIL_CHAR, valor)
        return CodeValue(dest, BRIL_CHAR, "Char")

    def const_void_object(self, tipo_cool="Object") -> CodeValue:
        tamanho = self.const_int(1)
        obj = self.novo_temp()
        self.emit_value("alloc", obj, PTR_ANY, [tamanho.nome_bril])
        objeto = CodeValue(obj, PTR_ANY, tipo_cool)
        tag = self.const_int(0)
        self.store_campo(objeto, 0, tag)
        return objeto

    def valor_padrao(self, tipo_cool: str) -> CodeValue:
        tipo_real = self.resolver_self_type(tipo_cool)
        if tipo_real == "Bool":
            return self.const_bool(False)
        if tipo_real == "Int":
            return self.const_int(0, tipo_cool="Int")
        if tipo_real == "String":
            return self.string_const("")
        return self.const_void_object(tipo_real)

    def coagir_para_tipo_bril(
        self, valor: CodeValue, tipo_bril_destino: Any
    ) -> CodeValue:
        if same_type(valor.tipo_bril, tipo_bril_destino):
            return valor
        if same_type(valor.tipo_bril, BRIL_BOOL) and same_type(
            tipo_bril_destino, BRIL_INT
        ):
            return self.bool_para_int(valor)
        if same_type(valor.tipo_bril, BRIL_INT) and same_type(
            tipo_bril_destino, BRIL_BOOL
        ):
            return self.int_para_bool(valor)
        if same_type(valor.tipo_bril, PTR_ANY) and same_type(
            tipo_bril_destino, BRIL_BOOL
        ):
            return self.ptr_para_bool(valor)
        if same_type(tipo_bril_destino, PTR_CHAR):
            return self.string_const("")
        if same_type(tipo_bril_destino, PTR_ANY):
            # Int/Bool/String não são boxeados nesta versão.
            if same_type(valor.tipo_bril, PTR_ANY):
                return valor
            return self.const_void_object("Object")
        if same_type(valor.tipo_bril, PTR_ANY) and same_type(
            tipo_bril_destino, BRIL_INT
        ):
            return self.const_int(0)
        raise CodegenError(
            f"Não foi possível converter valor Bril de {type_to_text(valor.tipo_bril)} "
            f"para {type_to_text(tipo_bril_destino)}."
        )

    def bool_para_int(self, valor: CodeValue) -> CodeValue:
        resultado = self.novo_temp()
        label_true = self.novo_label("bool_to_int_true")
        label_false = self.novo_label("bool_to_int_false")
        label_end = self.novo_label("bool_to_int_end")
        self.emit_effect("br", [valor.nome_bril], labels=[label_true, label_false])
        self.emit_label(label_true)
        um = self.const_int(1)
        self.emit_value("id", resultado, BRIL_INT, [um.nome_bril])
        self.emit_effect("jmp", labels=[label_end])
        self.emit_label(label_false)
        zero = self.const_int(0)
        self.emit_value("id", resultado, BRIL_INT, [zero.nome_bril])
        self.emit_effect("jmp", labels=[label_end])
        self.emit_label(label_end)
        return CodeValue(resultado, BRIL_INT, "Int")

    def int_para_bool(self, valor: CodeValue) -> CodeValue:
        zero = self.const_int(0)
        igual_zero = self.novo_temp()
        resultado = self.novo_temp()
        self.emit_value("eq", igual_zero, BRIL_BOOL, [valor.nome_bril, zero.nome_bril])
        self.emit_value("not", resultado, BRIL_BOOL, [igual_zero])
        return CodeValue(resultado, BRIL_BOOL, "Bool")

    def ptr_para_bool(self, valor: CodeValue) -> CodeValue:
        tag = self.load_campo(valor, 0, BRIL_INT, "Int")
        zero = self.const_int(0)
        eh_zero = self.novo_temp()
        resultado = self.novo_temp()
        self.emit_value("eq", eh_zero, BRIL_BOOL, [tag.nome_bril, zero.nome_bril])
        self.emit_value("not", resultado, BRIL_BOOL, [eh_zero])
        return CodeValue(resultado, BRIL_BOOL, "Bool")

    # ========================================================
    # Memória de objetos ptr<any>
    # ========================================================

    def campo_ptr(self, objeto: CodeValue, offset: int) -> str:
        if not same_type(objeto.tipo_bril, PTR_ANY):
            raise CodegenError(
                f"Tentativa de acessar campo de valor não ponteiro de objeto: {objeto}"
            )
        off = self.const_int(offset)
        ptr = self.novo_temp()
        self.emit_value("ptradd", ptr, PTR_ANY, [objeto.nome_bril, off.nome_bril])
        return ptr

    def store_campo(self, objeto: CodeValue, offset: int, valor: CodeValue):
        ptr = self.campo_ptr(objeto, offset)
        self.emit_effect("store", [ptr, valor.nome_bril])

    def load_campo(
        self, objeto: CodeValue, offset: int, tipo_bril: Any, tipo_cool: str
    ) -> CodeValue:
        ptr = self.campo_ptr(objeto, offset)
        dest = self.novo_temp()
        self.emit_value("load", dest, tipo_bril, [ptr])
        return CodeValue(dest, tipo_bril, tipo_cool)

    def novo_objeto(self, tipo_cool: str) -> CodeValue:
        tipo_real = self.resolver_self_type(tipo_cool)
        if tipo_real == "Int":
            return self.const_int(0, "Int")
        if tipo_real == "Bool":
            return self.const_bool(False)
        if tipo_real == "String":
            return self.string_const("")
        if tipo_real not in self.class_tags:
            return self.const_void_object(tipo_real)

        layout = self.attr_layouts.get(tipo_real, {})
        tamanho_objeto = self.class_sizes.get(tipo_real, len(layout) + 1)
        tamanho = self.const_int(tamanho_objeto)
        obj = self.novo_temp()
        self.emit_value("alloc", obj, PTR_ANY, [tamanho.nome_bril])
        objeto = CodeValue(obj, PTR_ANY, tipo_real)
        tag = self.const_int(self.class_tags.get(tipo_real, 0))
        self.store_campo(objeto, 0, tag)

        for _nome_attr, info in layout.items():
            atributo_ast = info["ast"]
            tipo_attr = info["tipo_cool"]
            offset = info["offset"]
            if atributo_ast.get("inicializacao") is not None:
                self.escopo.entrar()
                self.escopo.declarar("self", objeto.nome_bril, PTR_ANY, tipo_real)
                classe_antiga = self.current_class
                self.current_class = tipo_real
                valor = self.gerar_expr(atributo_ast["inicializacao"], valor_usado=True)
                self.current_class = classe_antiga
                self.escopo.sair()
            else:
                valor = self.valor_padrao(tipo_attr)
            valor = self.coagir_para_tipo_bril(valor, info["tipo_bril"])
            self.store_campo(objeto, offset, valor)
        return objeto

    # ========================================================
    # Memória de strings ptr<char>
    # ========================================================

    def char_ptr_offset_reg(self, base: CodeValue, offset_reg: str) -> str:
        if not same_type(base.tipo_bril, PTR_CHAR):
            raise CodegenError(f"Esperado ptr<char>, recebido {base}")
        ptr = self.novo_temp()
        self.emit_value("ptradd", ptr, PTR_CHAR, [base.nome_bril, offset_reg])
        return ptr

    def load_char_at_reg(self, base: CodeValue, offset_reg: str) -> CodeValue:
        ptr = self.char_ptr_offset_reg(base, offset_reg)
        dest = self.novo_temp()
        self.emit_value("load", dest, BRIL_CHAR, [ptr])
        return CodeValue(dest, BRIL_CHAR, "Char")

    def store_char_at_reg(self, base: CodeValue, offset_reg: str, ch: CodeValue):
        if not same_type(ch.tipo_bril, BRIL_CHAR):
            raise CodegenError("store_char_at_reg recebeu valor não-char")
        ptr = self.char_ptr_offset_reg(base, offset_reg)
        self.emit_effect("store", [ptr, ch.nome_bril])

    def string_const(self, texto: str) -> CodeValue:
        # Aloca chars + terminador NUL. Não fazemos interning global porque a região
        # precisa existir no fluxo da função atual.
        tamanho = self.const_int(len(texto) + 1)
        ptr = self.novo_temp()
        self.emit_value("alloc", ptr, PTR_CHAR, [tamanho.nome_bril])
        string_val = CodeValue(ptr, PTR_CHAR, "String")

        for i, ch in enumerate(texto):
            idx = self.const_int(i)
            ch_val = self.const_char(ch)
            self.store_char_at_reg(string_val, idx.nome_bril, ch_val)

        idx_nul = self.const_int(len(texto))
        nul = self.const_char("\x00")
        self.store_char_at_reg(string_val, idx_nul.nome_bril, nul)
        return string_val

    def string_length(self, s: CodeValue) -> CodeValue:
        s = self.coagir_para_tipo_bril(s, PTR_CHAR)
        i = self.novo_temp()
        zero = self.const_int(0)
        self.emit_value("id", i, BRIL_INT, [zero.nome_bril])
        label_cond = self.novo_label("strlen_cond")
        label_body = self.novo_label("strlen_body")
        label_end = self.novo_label("strlen_end")
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_cond)
        ch = self.load_char_at_reg(s, i)
        nul = self.const_char("\x00")
        is_end = self.novo_temp()
        self.emit_value("ceq", is_end, BRIL_BOOL, [ch.nome_bril, nul.nome_bril])
        self.emit_effect("br", [is_end], labels=[label_end, label_body])
        self.emit_label(label_body)
        one = self.const_int(1)
        inc = self.novo_temp()
        self.emit_value("add", inc, BRIL_INT, [i, one.nome_bril])
        self.emit_value("id", i, BRIL_INT, [inc])
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_end)
        return CodeValue(i, BRIL_INT, "Int")

    def string_copy_chars(
        self, src: CodeValue, dest: CodeValue, dest_start: str, length: CodeValue
    ):
        i = self.novo_temp()
        zero = self.const_int(0)
        self.emit_value("id", i, BRIL_INT, [zero.nome_bril])
        label_cond = self.novo_label("strcopy_cond")
        label_body = self.novo_label("strcopy_body")
        label_end = self.novo_label("strcopy_end")
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_cond)
        cond = self.novo_temp()
        self.emit_value("lt", cond, BRIL_BOOL, [i, length.nome_bril])
        self.emit_effect("br", [cond], labels=[label_body, label_end])
        self.emit_label(label_body)
        ch = self.load_char_at_reg(src, i)
        dest_idx = self.novo_temp()
        self.emit_value("add", dest_idx, BRIL_INT, [dest_start, i])
        self.store_char_at_reg(dest, dest_idx, ch)
        one = self.const_int(1)
        inc = self.novo_temp()
        self.emit_value("add", inc, BRIL_INT, [i, one.nome_bril])
        self.emit_value("id", i, BRIL_INT, [inc])
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_end)

    def string_concat(self, a: CodeValue, b: CodeValue) -> CodeValue:
        a = self.coagir_para_tipo_bril(a, PTR_CHAR)
        b = self.coagir_para_tipo_bril(b, PTR_CHAR)
        len_a = self.string_length(a)
        len_b = self.string_length(b)
        soma = self.novo_temp()
        self.emit_value("add", soma, BRIL_INT, [len_a.nome_bril, len_b.nome_bril])
        one = self.const_int(1)
        size = self.novo_temp()
        self.emit_value("add", size, BRIL_INT, [soma, one.nome_bril])
        ptr = self.novo_temp()
        self.emit_value("alloc", ptr, PTR_CHAR, [size])
        out = CodeValue(ptr, PTR_CHAR, "String")
        zero = self.const_int(0)
        self.string_copy_chars(a, out, zero.nome_bril, len_a)
        self.string_copy_chars(b, out, len_a.nome_bril, len_b)
        nul = self.const_char("\x00")
        self.store_char_at_reg(out, soma, nul)
        return out

    def string_substr(
        self, s: CodeValue, start: CodeValue, length: CodeValue
    ) -> CodeValue:
        s = self.coagir_para_tipo_bril(s, PTR_CHAR)
        start = self.coagir_para_tipo_bril(start, BRIL_INT)
        length = self.coagir_para_tipo_bril(length, BRIL_INT)
        one = self.const_int(1)
        size = self.novo_temp()
        self.emit_value("add", size, BRIL_INT, [length.nome_bril, one.nome_bril])
        ptr = self.novo_temp()
        self.emit_value("alloc", ptr, PTR_CHAR, [size])
        out = CodeValue(ptr, PTR_CHAR, "String")

        i = self.novo_temp()
        zero = self.const_int(0)
        self.emit_value("id", i, BRIL_INT, [zero.nome_bril])
        label_cond = self.novo_label("substr_cond")
        label_body = self.novo_label("substr_body")
        label_end = self.novo_label("substr_end")
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_cond)
        cond = self.novo_temp()
        self.emit_value("lt", cond, BRIL_BOOL, [i, length.nome_bril])
        self.emit_effect("br", [cond], labels=[label_body, label_end])
        self.emit_label(label_body)
        src_idx = self.novo_temp()
        self.emit_value("add", src_idx, BRIL_INT, [start.nome_bril, i])
        ch = self.load_char_at_reg(s, src_idx)
        self.store_char_at_reg(out, i, ch)
        inc = self.novo_temp()
        self.emit_value("add", inc, BRIL_INT, [i, one.nome_bril])
        self.emit_value("id", i, BRIL_INT, [inc])
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_end)
        nul = self.const_char("\x00")
        self.store_char_at_reg(out, length.nome_bril, nul)
        return out

    def string_equals(self, a: CodeValue, b: CodeValue) -> CodeValue:
        a = self.coagir_para_tipo_bril(a, PTR_CHAR)
        b = self.coagir_para_tipo_bril(b, PTR_CHAR)
        i = self.novo_temp()
        zero = self.const_int(0)
        self.emit_value("id", i, BRIL_INT, [zero.nome_bril])
        result = self.novo_temp()
        label_cond = self.novo_label("streq_cond")
        label_false = self.novo_label("streq_false")
        label_true = self.novo_label("streq_true")
        label_next = self.novo_label("streq_next")
        label_end = self.novo_label("streq_end")
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_cond)
        ca = self.load_char_at_reg(a, i)
        cb = self.load_char_at_reg(b, i)
        eq_chars = self.novo_temp()
        self.emit_value("ceq", eq_chars, BRIL_BOOL, [ca.nome_bril, cb.nome_bril])
        self.emit_effect("br", [eq_chars], labels=[label_next, label_false])
        self.emit_label(label_next)
        nul = self.const_char("\x00")
        end_a = self.novo_temp()
        self.emit_value("ceq", end_a, BRIL_BOOL, [ca.nome_bril, nul.nome_bril])
        self.emit_effect("br", [end_a], labels=[label_true, label_cond + "_inc"])
        self.emit_label(label_cond + "_inc")
        one = self.const_int(1)
        inc = self.novo_temp()
        self.emit_value("add", inc, BRIL_INT, [i, one.nome_bril])
        self.emit_value("id", i, BRIL_INT, [inc])
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_false)
        f = self.const_bool(False)
        self.emit_value("id", result, BRIL_BOOL, [f.nome_bril])
        self.emit_effect("jmp", labels=[label_end])
        self.emit_label(label_true)
        t = self.const_bool(True)
        self.emit_value("id", result, BRIL_BOOL, [t.nome_bril])
        self.emit_effect("jmp", labels=[label_end])
        self.emit_label(label_end)
        return CodeValue(result, BRIL_BOOL, "Bool")

    def print_string_chars(self, s: CodeValue):
        s = self.coagir_para_tipo_bril(s, PTR_CHAR)
        i = self.novo_temp()
        zero = self.const_int(0)
        self.emit_value("id", i, BRIL_INT, [zero.nome_bril])
        label_cond = self.novo_label("printstr_cond")
        label_body = self.novo_label("printstr_body")
        label_end = self.novo_label("printstr_end")
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_cond)
        ch = self.load_char_at_reg(s, i)
        nul = self.const_char("\x00")
        is_end = self.novo_temp()
        self.emit_value("ceq", is_end, BRIL_BOOL, [ch.nome_bril, nul.nome_bril])
        self.emit_effect("br", [is_end], labels=[label_end, label_body])
        self.emit_label(label_body)
        self.emit_effect("print", [ch.nome_bril])
        one = self.const_int(1)
        inc = self.novo_temp()
        self.emit_value("add", inc, BRIL_INT, [i, one.nome_bril])
        self.emit_value("id", i, BRIL_INT, [inc])
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_end)

    def print_string_literal_as_chars(self, texto: str):
        if not texto:
            self.emit_effect("nop")
            return
        args = []
        for ch in texto:
            cv = self.const_char(ch)
            args.append(cv.nome_bril)
        self.emit_effect("print", args)

    # ========================================================
    # Funções e wrappers
    # ========================================================

    def gerar_metodo(self, classe_nome: str, metodo: dict) -> dict:
        self.resetar_estado_funcao(classe_nome)
        nome_metodo = metodo["nome"]
        tipo_retorno_cool = metodo["tipo_retorno"]
        tipo_retorno_bril = self.tipo_bril(tipo_retorno_cool)
        args = []
        nome_self = self.novo_nome_usuario("self")
        self.escopo.declarar("self", nome_self, PTR_ANY, classe_nome)
        args.append({"name": nome_self, "type": PTR_ANY})
        self.escopo.entrar()

        if self.usar_main_input and classe_nome == "Main" and nome_metodo == "main":
            for i in range(self.main_int_input_count):
                nome_input = f"__cool_input{i}"
                self.escopo.declarar(nome_input, nome_input, BRIL_INT, "Int")
                args.append({"name": nome_input, "type": BRIL_INT})

        for formal in metodo.get("formais", []):
            nome_cool = formal["nome"]
            tipo_cool = formal["tipo"]
            tipo_bril = self.tipo_bril(tipo_cool)
            nome_bril = self.novo_nome_usuario(nome_cool)
            self.escopo.declarar(nome_cool, nome_bril, tipo_bril, tipo_cool)
            args.append({"name": nome_bril, "type": tipo_bril})

        valor_corpo = self.gerar_expr(metodo["corpo"], valor_usado=True)
        valor_corpo = self.coagir_para_tipo_bril(valor_corpo, tipo_retorno_bril)
        self.emit_effect("ret", [valor_corpo.nome_bril])
        self.escopo.sair()
        return {
            "name": self.nome_funcao_impl(classe_nome, nome_metodo),
            "args": args,
            "type": tipo_retorno_bril,
            "instrs": self.instrs,
        }

    def gerar_funcao_new_main(self) -> dict:
        self.resetar_estado_funcao("Main")
        main_obj = self.novo_objeto("Main")
        self.emit_effect("ret", [main_obj.nome_bril])
        return {"name": "__cool_new_Main", "type": PTR_ANY, "instrs": self.instrs}

    def gerar_wrapper_main(self) -> dict:
        metodo_main = self.buscar_metodo("Main", "main")
        if metodo_main is None:
            raise CodegenError("Não foi encontrado método Main.main para gerar main.")
        tipo_main_cool = metodo_main["tipo_retorno"]
        tipo_main_bril = self.tipo_bril(tipo_main_cool)
        self.resetar_estado_funcao("Main")
        args = []
        if self.usar_main_input:
            for i in range(self.main_int_input_count):
                args.append({"name": f"__input{i}", "type": BRIL_INT})
        main_obj = self.novo_temp()
        self.emit_value("call", main_obj, PTR_ANY, funcs=["__cool_new_Main"])
        result = self.novo_temp()
        call_args = [main_obj]
        if self.usar_main_input:
            for i in range(self.main_int_input_count):
                call_args.append(f"__input{i}")
        self.emit_value("call", result, tipo_main_bril, call_args, funcs=["Main_main"])
        if self.imprimir_resultado and tipo_main_cool in {"Int", "Bool"}:
            self.emit_effect("print", [result])
        self.emit_effect("ret")
        fn = {"name": "main", "instrs": self.instrs}
        if args:
            fn["args"] = args
        return fn

    def gerar_wrapper_dispatch(self, classe_nome: str, metodo_nome: str) -> dict:
        metodo_base = self.classes[classe_nome]["metodos"].get(metodo_nome)
        if metodo_base is None:
            raise CodegenError(
                f"Wrapper solicitado para método inexistente: {classe_nome}.{metodo_nome}"
            )
        self.resetar_estado_funcao(classe_nome)
        tipo_retorno_cool = metodo_base["tipo_retorno"]
        tipo_retorno_bril = self.tipo_bril(tipo_retorno_cool)
        args = [{"name": "self", "type": PTR_ANY}]
        args_chamada = ["self"]
        self.escopo.declarar("self", "self", PTR_ANY, classe_nome)
        for formal_nome, formal_tipo in metodo_base.get("formais", []):
            tipo_bril = self.tipo_bril(formal_tipo)
            nome_arg = self.sanitizar(formal_nome)
            args.append({"name": nome_arg, "type": tipo_bril})
            args_chamada.append(nome_arg)
            self.escopo.declarar(formal_nome, nome_arg, tipo_bril, formal_tipo)
        self_obj = CodeValue("self", PTR_ANY, classe_nome)
        tag_objeto = self.load_campo(self_obj, 0, BRIL_INT, "Int")
        resultado = self.novo_temp()
        label_fim = self.novo_label("dispatch_end")

        classes_possiveis = self.descendentes_incluindo_classe(classe_nome)
        for classe_dinamica in classes_possiveis:
            definidora = self.classe_definidora_metodo(classe_dinamica, metodo_nome)
            if definidora is None:
                continue
            tag_valor = self.class_tags.get(classe_dinamica)
            if tag_valor is None:
                continue
            tag_const = self.const_int(tag_valor)
            cond = self.novo_temp()
            label_hit = self.novo_label(f"dispatch_{classe_dinamica}")
            label_miss = self.novo_label(f"dispatch_miss_{classe_dinamica}")
            self.emit_value(
                "eq", cond, BRIL_BOOL, [tag_objeto.nome_bril, tag_const.nome_bril]
            )
            self.emit_effect("br", [cond], labels=[label_hit, label_miss])
            self.emit_label(label_hit)
            nome_impl = self.nome_funcao_impl(definidora, metodo_nome)
            ret_tmp = self.novo_temp()
            self.emit_value(
                "call", ret_tmp, tipo_retorno_bril, args_chamada, funcs=[nome_impl]
            )
            self.emit_value("id", resultado, tipo_retorno_bril, [ret_tmp])
            self.emit_effect("jmp", labels=[label_fim])
            self.emit_label(label_miss)

        nome_fallback = self.nome_funcao_impl(classe_nome, metodo_nome)
        ret_fallback = self.novo_temp()
        self.emit_value(
            "call", ret_fallback, tipo_retorno_bril, args_chamada, funcs=[nome_fallback]
        )
        self.emit_value("id", resultado, tipo_retorno_bril, [ret_fallback])
        self.emit_label(label_fim)
        self.emit_effect("ret", [resultado])
        return {
            "name": self.nome_funcao(classe_nome, metodo_nome),
            "args": args,
            "type": tipo_retorno_bril,
            "instrs": self.instrs,
        }

    # ========================================================
    # Expressões
    # ========================================================

    def gerar_expr(self, expr: dict, valor_usado=True) -> CodeValue:
        tipo_no = expr["no"]
        if tipo_no == "Inteiro":
            return self.const_int(expr["valor"], "Int")
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
            return self.gerar_if(expr, valor_usado)
        if tipo_no == "While":
            return self.gerar_while(expr, valor_usado)
        if tipo_no == "Bloco":
            return self.gerar_bloco(expr, valor_usado)
        if tipo_no == "Let":
            return self.gerar_let(expr, valor_usado)
        if tipo_no == "Case":
            return self.gerar_case(expr, valor_usado)
        if tipo_no == "ChamadaSimples":
            return self.gerar_chamada_simples(expr, valor_usado)
        if tipo_no == "Dispatch":
            return self.gerar_dispatch(expr, valor_usado)
        if tipo_no == "DispatchEstatico":
            return self.gerar_dispatch_estatico(expr, valor_usado)
        raise CodegenError(f"Nó de expressão não reconhecido: {tipo_no}")

    def gerar_identificador(self, expr: dict) -> CodeValue:
        nome = expr["nome"]
        var = self.escopo.buscar(nome)
        if var is not None:
            return CodeValue(var.nome_bril, var.tipo_bril, var.tipo_cool)
        layout = self.attr_layouts.get(self.current_class, {})
        if nome in layout:
            self_var = self.escopo.buscar("self")
            if self_var is None:
                raise CodegenError(f"Atributo '{nome}' usado sem self disponível.")
            objeto = CodeValue(
                self_var.nome_bril, self_var.tipo_bril, self_var.tipo_cool
            )
            info = layout[nome]
            return self.load_campo(
                objeto, info["offset"], info["tipo_bril"], info["tipo_cool"]
            )
        raise CodegenError(f"Identificador não encontrado: {nome}")

    def gerar_atribuicao(self, expr: dict) -> CodeValue:
        nome = expr["nome"]
        valor = self.gerar_expr(expr["valor"], valor_usado=True)
        var = self.escopo.buscar(nome)
        if var is not None:
            valor = self.coagir_para_tipo_bril(valor, var.tipo_bril)
            self.emit_value("id", var.nome_bril, var.tipo_bril, [valor.nome_bril])
            return CodeValue(var.nome_bril, var.tipo_bril, var.tipo_cool)
        layout = self.attr_layouts.get(self.current_class, {})
        if nome in layout:
            self_var = self.escopo.buscar("self")
            if self_var is None:
                raise CodegenError(f"Atributo '{nome}' atribuído sem self disponível.")
            objeto = CodeValue(
                self_var.nome_bril, self_var.tipo_bril, self_var.tipo_cool
            )
            info = layout[nome]
            valor = self.coagir_para_tipo_bril(valor, info["tipo_bril"])
            self.store_campo(objeto, info["offset"], valor)
            return valor
        raise CodegenError(f"Atribuição para identificador não encontrado: {nome}")

    def gerar_binario(self, expr: dict) -> CodeValue:
        op = expr["operador"]
        esq = self.gerar_expr(expr["esquerda"], valor_usado=True)
        dir_ = self.gerar_expr(expr["direita"], valor_usado=True)
        if op in {"+", "-", "*", "/"}:
            esq = self.coagir_para_tipo_bril(esq, BRIL_INT)
            dir_ = self.coagir_para_tipo_bril(dir_, BRIL_INT)
            op_bril = {"+": "add", "-": "sub", "*": "mul", "/": "div"}[op]
            dest = self.novo_temp()
            self.emit_value(op_bril, dest, BRIL_INT, [esq.nome_bril, dir_.nome_bril])
            return CodeValue(dest, BRIL_INT, "Int")
        if op in {"<", "<="}:
            esq = self.coagir_para_tipo_bril(esq, BRIL_INT)
            dir_ = self.coagir_para_tipo_bril(dir_, BRIL_INT)
            op_bril = {"<": "lt", "<=": "le"}[op]
            dest = self.novo_temp()
            self.emit_value(op_bril, dest, BRIL_BOOL, [esq.nome_bril, dir_.nome_bril])
            return CodeValue(dest, BRIL_BOOL, "Bool")
        if op == "=":
            return self.gerar_igualdade(esq, dir_)
        raise CodegenError(f"Operador binário não suportado: {op}")

    def gerar_igualdade(self, esq: CodeValue, dir_: CodeValue) -> CodeValue:
        if same_type(esq.tipo_bril, BRIL_BOOL) and same_type(dir_.tipo_bril, BRIL_BOOL):
            not_esq = self.novo_temp()
            not_dir = self.novo_temp()
            ambos_true = self.novo_temp()
            ambos_false = self.novo_temp()
            resultado = self.novo_temp()
            self.emit_value("not", not_esq, BRIL_BOOL, [esq.nome_bril])
            self.emit_value("not", not_dir, BRIL_BOOL, [dir_.nome_bril])
            self.emit_value(
                "and", ambos_true, BRIL_BOOL, [esq.nome_bril, dir_.nome_bril]
            )
            self.emit_value("and", ambos_false, BRIL_BOOL, [not_esq, not_dir])
            self.emit_value("or", resultado, BRIL_BOOL, [ambos_true, ambos_false])
            return CodeValue(resultado, BRIL_BOOL, "Bool")
        if same_type(esq.tipo_bril, BRIL_INT) and same_type(dir_.tipo_bril, BRIL_INT):
            dest = self.novo_temp()
            self.emit_value("eq", dest, BRIL_BOOL, [esq.nome_bril, dir_.nome_bril])
            return CodeValue(dest, BRIL_BOOL, "Bool")
        if same_type(esq.tipo_bril, PTR_CHAR) and same_type(dir_.tipo_bril, PTR_CHAR):
            return self.string_equals(esq, dir_)
        if same_type(esq.tipo_bril, PTR_ANY) and same_type(dir_.tipo_bril, PTR_ANY):
            tag_esq = self.load_campo(esq, 0, BRIL_INT, "Int")
            tag_dir = self.load_campo(dir_, 0, BRIL_INT, "Int")
            dest = self.novo_temp()
            self.emit_value(
                "eq", dest, BRIL_BOOL, [tag_esq.nome_bril, tag_dir.nome_bril]
            )
            return CodeValue(dest, BRIL_BOOL, "Bool")
        return self.const_bool(False)

    def gerar_negacao_aritmetica(self, expr: dict) -> CodeValue:
        valor = self.gerar_expr(expr["expressao"], valor_usado=True)
        valor = self.coagir_para_tipo_bril(valor, BRIL_INT)
        zero = self.const_int(0)
        dest = self.novo_temp()
        self.emit_value("sub", dest, BRIL_INT, [zero.nome_bril, valor.nome_bril])
        return CodeValue(dest, BRIL_INT, "Int")

    def gerar_not(self, expr: dict) -> CodeValue:
        valor = self.gerar_expr(expr["expressao"], valor_usado=True)
        valor = self.coagir_para_tipo_bril(valor, BRIL_BOOL)
        dest = self.novo_temp()
        self.emit_value("not", dest, BRIL_BOOL, [valor.nome_bril])
        return CodeValue(dest, BRIL_BOOL, "Bool")

    def gerar_isvoid(self, expr: dict) -> CodeValue:
        valor = self.gerar_expr(expr["expressao"], valor_usado=True)
        if not same_type(valor.tipo_bril, PTR_ANY):
            return self.const_bool(False)
        tag = self.load_campo(valor, 0, BRIL_INT, "Int")
        zero = self.const_int(0)
        dest = self.novo_temp()
        self.emit_value("eq", dest, BRIL_BOOL, [tag.nome_bril, zero.nome_bril])
        return CodeValue(dest, BRIL_BOOL, "Bool")

    def gerar_new(self, expr: dict) -> CodeValue:
        return self.novo_objeto(expr["tipo"])

    def gerar_if(self, expr: dict, valor_usado=True) -> CodeValue:
        cond = self.gerar_expr(expr["condicao"], valor_usado=True)
        cond = self.coagir_para_tipo_bril(cond, BRIL_BOOL)
        label_then = self.novo_label("if_then")
        label_else = self.novo_label("if_else")
        label_fim = self.novo_label("if_end")
        tipo_resultado_bril = self.planejar_tipo_bril(expr)
        resultado = self.novo_temp()
        self.emit_effect("br", [cond.nome_bril], labels=[label_then, label_else])
        self.emit_label(label_then)
        valor_then = self.gerar_expr(expr["entao"], valor_usado=valor_usado)
        if valor_usado:
            valor_then = self.coagir_para_tipo_bril(valor_then, tipo_resultado_bril)
            self.emit_value(
                "id", resultado, tipo_resultado_bril, [valor_then.nome_bril]
            )
        self.emit_effect("jmp", labels=[label_fim])
        self.emit_label(label_else)
        valor_else = self.gerar_expr(expr["senao"], valor_usado=valor_usado)
        if valor_usado:
            valor_else = self.coagir_para_tipo_bril(valor_else, tipo_resultado_bril)
            self.emit_value(
                "id", resultado, tipo_resultado_bril, [valor_else.nome_bril]
            )
        self.emit_effect("jmp", labels=[label_fim])
        self.emit_label(label_fim)
        if valor_usado:
            tipo_cool = expr.get("tipo_inferido") or (
                "Bool" if same_type(tipo_resultado_bril, BRIL_BOOL) else "Object"
            )
            return CodeValue(resultado, tipo_resultado_bril, tipo_cool)
        return self.valor_padrao("Object")

    def gerar_while(self, expr: dict, valor_usado=True) -> CodeValue:
        label_cond = self.novo_label("while_cond")
        label_body = self.novo_label("while_body")
        label_fim = self.novo_label("while_end")
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_cond)
        cond = self.gerar_expr(expr["condicao"], valor_usado=True)
        cond = self.coagir_para_tipo_bril(cond, BRIL_BOOL)
        self.emit_effect("br", [cond.nome_bril], labels=[label_body, label_fim])
        self.emit_label(label_body)
        self.gerar_expr(expr["corpo"], valor_usado=False)
        self.emit_effect("jmp", labels=[label_cond])
        self.emit_label(label_fim)
        return self.valor_padrao("Object")

    def gerar_bloco(self, expr: dict, valor_usado=True) -> CodeValue:
        expressoes = expr.get("expressoes", [])
        if not expressoes:
            return self.valor_padrao("Object")
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
                valor_init = self.gerar_expr(decl["inicializacao"], valor_usado=True)
            else:
                valor_init = self.valor_padrao(tipo_cool)
            valor_init = self.coagir_para_tipo_bril(valor_init, tipo_bril)
            self.escopo.declarar(nome_cool, nome_bril, tipo_bril, tipo_cool)
            self.emit_value("id", nome_bril, tipo_bril, [valor_init.nome_bril])
        valor_corpo = self.gerar_expr(expr["corpo"], valor_usado=valor_usado)
        self.escopo.sair()
        return valor_corpo

    def gerar_case(self, expr: dict, valor_usado=True) -> CodeValue:
        valor_base = self.gerar_expr(expr["expressao"], valor_usado=True)
        ramos = expr.get("ramos", [])
        if not ramos:
            return self.valor_padrao("Object")
        tipo_resultado_bril = self.planejar_tipo_bril(expr)
        resultado = self.novo_temp()
        if not same_type(valor_base.tipo_bril, PTR_ANY):
            ramo = ramos[0]
            self.escopo.entrar()
            tipo_ramo = ramo["tipo"]
            tipo_bril = self.tipo_bril(tipo_ramo)
            nome_bril = self.novo_nome_usuario(ramo["nome"])
            valor_coagido = self.coagir_para_tipo_bril(valor_base, tipo_bril)
            self.escopo.declarar(ramo["nome"], nome_bril, tipo_bril, tipo_ramo)
            self.emit_value("id", nome_bril, tipo_bril, [valor_coagido.nome_bril])
            valor_ramo = self.gerar_expr(ramo["expressao"], valor_usado=valor_usado)
            self.escopo.sair()
            if valor_usado:
                valor_ramo = self.coagir_para_tipo_bril(valor_ramo, tipo_resultado_bril)
                self.emit_value(
                    "id", resultado, tipo_resultado_bril, [valor_ramo.nome_bril]
                )
                return CodeValue(
                    resultado, tipo_resultado_bril, expr.get("tipo_inferido", "Object")
                )
            return self.valor_padrao("Object")

        tag_objeto = self.load_campo(valor_base, 0, BRIL_INT, "Int")
        label_fim = self.novo_label("case_end")
        classes_dinamicas = sorted(
            self.class_tags.keys(), key=lambda c: self.class_tags[c]
        )
        for classe_dinamica in classes_dinamicas:
            ramo_escolhido = self.escolher_ramo_case_para_classe(ramos, classe_dinamica)
            if ramo_escolhido is None:
                continue
            tag_const = self.const_int(self.class_tags[classe_dinamica])
            cond = self.novo_temp()
            label_hit = self.novo_label(f"case_{classe_dinamica}")
            label_miss = self.novo_label(f"case_miss_{classe_dinamica}")
            self.emit_value(
                "eq", cond, BRIL_BOOL, [tag_objeto.nome_bril, tag_const.nome_bril]
            )
            self.emit_effect("br", [cond], labels=[label_hit, label_miss])
            self.emit_label(label_hit)
            self.escopo.entrar()
            tipo_ramo = ramo_escolhido["tipo"]
            tipo_bril = self.tipo_bril(tipo_ramo)
            nome_bril = self.novo_nome_usuario(ramo_escolhido["nome"])
            valor_ramo_base = self.coagir_para_tipo_bril(valor_base, tipo_bril)
            self.escopo.declarar(
                ramo_escolhido["nome"], nome_bril, tipo_bril, tipo_ramo
            )
            self.emit_value("id", nome_bril, tipo_bril, [valor_ramo_base.nome_bril])
            valor_ramo = self.gerar_expr(
                ramo_escolhido["expressao"], valor_usado=valor_usado
            )
            self.escopo.sair()
            if valor_usado:
                valor_ramo = self.coagir_para_tipo_bril(valor_ramo, tipo_resultado_bril)
                self.emit_value(
                    "id", resultado, tipo_resultado_bril, [valor_ramo.nome_bril]
                )
            self.emit_effect("jmp", labels=[label_fim])
            self.emit_label(label_miss)
        fallback_tipo = (
            "Object"
            if same_type(tipo_resultado_bril, PTR_ANY)
            else ("Bool" if same_type(tipo_resultado_bril, BRIL_BOOL) else "Int")
        )
        fallback = self.valor_padrao(fallback_tipo)
        fallback = self.coagir_para_tipo_bril(fallback, tipo_resultado_bril)
        if valor_usado:
            self.emit_value("id", resultado, tipo_resultado_bril, [fallback.nome_bril])
        self.emit_label(label_fim)
        if valor_usado:
            return CodeValue(
                resultado, tipo_resultado_bril, expr.get("tipo_inferido", "Object")
            )
        return self.valor_padrao("Object")

    def escolher_ramo_case_para_classe(
        self, ramos: List[dict], classe_dinamica: str
    ) -> Optional[dict]:
        melhor = None
        melhor_dist = None
        for ramo in ramos:
            tipo_ramo = self.resolver_self_type(ramo["tipo"])
            dist = self.distancia_ate_ancestral(classe_dinamica, tipo_ramo)
            if dist is None:
                continue
            if melhor is None or dist < melhor_dist:
                melhor = ramo
                melhor_dist = dist
        return melhor

    # ========================================================
    # Chamadas
    # ========================================================

    def gerar_chamada_simples(self, expr: dict, valor_usado=True) -> CodeValue:
        self_var = self.escopo.buscar("self")
        if self_var is None:
            receiver = self.valor_padrao(self.current_class or "Object")
        else:
            receiver = CodeValue(
                self_var.nome_bril, self_var.tipo_bril, self_var.tipo_cool
            )
        return self.gerar_chamada_metodo(
            receiver,
            expr["metodo"],
            expr.get("argumentos", []),
            receiver.tipo_cool,
            valor_usado,
        )

    def gerar_dispatch(self, expr: dict, valor_usado=True) -> CodeValue:
        receiver = self.gerar_expr(expr["alvo"], valor_usado=True)
        return self.gerar_chamada_metodo(
            receiver,
            expr["metodo"],
            expr.get("argumentos", []),
            receiver.tipo_cool,
            valor_usado,
        )

    def gerar_dispatch_estatico(self, expr: dict, valor_usado=True) -> CodeValue:
        receiver = self.gerar_expr(expr["alvo"], valor_usado=True)
        return self.gerar_chamada_metodo(
            receiver,
            expr["metodo"],
            expr.get("argumentos", []),
            expr["tipo_estatico"],
            valor_usado,
            dispatch_estatico=True,
        )

    def gerar_chamada_metodo(
        self,
        receiver: CodeValue,
        metodo_nome: str,
        argumentos_expr: List[dict],
        classe_busca: str,
        valor_usado=True,
        dispatch_estatico=False,
    ) -> CodeValue:
        classe_busca = self.resolver_self_type(classe_busca)
        metodo = self.buscar_metodo(classe_busca, metodo_nome)
        if metodo is None:
            self.emit_effect("nop")
            return self.valor_padrao("Object")
        if metodo["basico"]:
            return self.gerar_chamada_basica(
                receiver, metodo, argumentos_expr, valor_usado=valor_usado
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
        nome_funcao = (
            self.nome_funcao_impl(metodo["classe"], metodo["nome"])
            if dispatch_estatico
            else self.nome_funcao_chamada(metodo["classe"], metodo["nome"])
        )
        todos_args = [receiver.nome_bril] + args_bril
        self.emit_value(
            "call", dest, tipo_retorno_bril, todos_args, funcs=[nome_funcao]
        )
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
            self.emit_effect("nop")
            return self.valor_padrao("Object")
        if nome == "type_name":
            return self.string_const(receiver.tipo_cool)
        if nome == "copy":
            return receiver
        if nome == "out_int":
            if argumentos_expr:
                arg = self.gerar_expr(argumentos_expr[0], valor_usado=True)
                arg = self.coagir_para_tipo_bril(arg, BRIL_INT)
                self.emit_effect("print", [arg.nome_bril])
            return receiver
        if nome == "out_string":
            if argumentos_expr:
                # Para literais, evita montar a string só para imprimir.
                if (
                    argumentos_expr[0].get("no") == "StringLiteral"
                    and not self.debug_string_ids
                ):
                    self.print_string_literal_as_chars(argumentos_expr[0]["valor"])
                else:
                    arg = self.gerar_expr(argumentos_expr[0], valor_usado=True)
                    if self.debug_string_ids:
                        length = self.string_length(arg)
                        self.emit_effect("print", [length.nome_bril])
                    else:
                        self.print_string_chars(arg)
            else:
                self.emit_effect("nop")
            return receiver
        if nome == "in_int":
            # Bril padrão recebe entradas como argumentos de @main, não por leitura
            # interativa. Em Main.main, cada chamada consome o próximo __cool_inputN.
            if self.usar_main_input:
                entrada = self.escopo.buscar(f"__cool_input{self.input_int_counter}")
                self.input_int_counter += 1
                if entrada is not None:
                    return CodeValue(
                        entrada.nome_bril, entrada.tipo_bril, entrada.tipo_cool
                    )
            return self.const_int(0, "Int")
        if nome == "in_string":
            # Não há entrada textual interativa padrão em Bril. Para testes, permite
            # injetar strings em tempo de compilação com --main-string.
            if self.input_string_counter < len(self.main_string_inputs):
                texto = self.main_string_inputs[self.input_string_counter]
                self.input_string_counter += 1
                return self.string_const(texto)
            self.input_string_counter += 1
            return self.string_const("")
        if nome == "length":
            return self.string_length(receiver)
        if nome == "concat":
            arg = (
                self.gerar_expr(argumentos_expr[0], valor_usado=True)
                if argumentos_expr
                else self.string_const("")
            )
            return self.string_concat(receiver, arg)
        if nome == "substr":
            start = (
                self.gerar_expr(argumentos_expr[0], valor_usado=True)
                if len(argumentos_expr) >= 1
                else self.const_int(0)
            )
            length = (
                self.gerar_expr(argumentos_expr[1], valor_usado=True)
                if len(argumentos_expr) >= 2
                else self.const_int(0)
            )
            return self.string_substr(receiver, start, length)
        self.emit_effect("nop")
        return self.valor_padrao("Object")

    # ========================================================
    # Planejamento simples de tipo Bril
    # ========================================================

    def planejar_tipo_bril(self, expr: dict) -> Any:
        tipo_inferido = expr.get("tipo_inferido")
        if tipo_inferido is not None:
            return self.tipo_bril(tipo_inferido)
        tipo_no = expr.get("no")
        if tipo_no == "Booleano":
            return BRIL_BOOL
        if tipo_no == "Inteiro":
            return BRIL_INT
        if tipo_no == "StringLiteral":
            return PTR_CHAR
        if tipo_no == "Identificador":
            var = self.escopo.buscar(expr["nome"])
            if var:
                return var.tipo_bril
            layout = self.attr_layouts.get(self.current_class, {})
            info = layout.get(expr["nome"])
            if info:
                return info["tipo_bril"]
            return PTR_ANY
        if tipo_no == "Binario":
            return BRIL_BOOL if expr["operador"] in {"<", "<=", "="} else BRIL_INT
        if tipo_no in {"Not", "Isvoid"}:
            return BRIL_BOOL
        if tipo_no == "NegacaoAritmetica":
            return BRIL_INT
        if tipo_no == "If":
            tipo_then = self.planejar_tipo_bril(expr["entao"])
            tipo_else = self.planejar_tipo_bril(expr["senao"])
            return tipo_then if same_type(tipo_then, tipo_else) else PTR_ANY
        if tipo_no == "While":
            return PTR_ANY
        if tipo_no == "Bloco":
            expressoes = expr.get("expressoes", [])
            return self.planejar_tipo_bril(expressoes[-1]) if expressoes else PTR_ANY
        if tipo_no == "Let":
            return self.planejar_tipo_bril(expr["corpo"])
        if tipo_no == "Case":
            ramos = expr.get("ramos", [])
            if not ramos:
                return PTR_ANY
            tipos = [self.planejar_tipo_bril(r["expressao"]) for r in ramos]
            primeiro = tipos[0]
            return primeiro if all(same_type(t, primeiro) for t in tipos) else PTR_ANY
        if tipo_no == "ChamadaSimples":
            self_var = self.escopo.buscar("self")
            classe = self_var.tipo_cool if self_var else self.current_class
            metodo = self.buscar_metodo(classe or "Object", expr["metodo"])
            return self.tipo_bril(metodo["tipo_retorno"]) if metodo else PTR_ANY
        if tipo_no in {"Dispatch", "DispatchEstatico"}:
            return self.tipo_bril(tipo_inferido) if tipo_inferido else PTR_ANY
        if tipo_no == "New":
            return self.tipo_bril(expr["tipo"])
        return PTR_ANY


# ============================================================
# Pipeline completo
# ============================================================


def compilar_para_bril(
    caminho_codigo: str,
    imprimir_resultado=True,
    strict_semantic=False,
    debug_string_ids=False,
    usar_main_input=False,
    main_int_inputs_count: Optional[int] = None,
    main_string_inputs: Optional[List[str]] = None,
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
                "Aviso: a análise semântica encontrou erro(s), mas a geração Bril continuará em modo permissivo.",
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
            "Aviso: a análise semântica falhou, mas a geração Bril continuará em modo permissivo.",
            file=sys.stderr,
        )
        print(str(erro), file=sys.stderr)

    gerador = BrilCodeGenerator(
        ast=ast,
        tabela_classes=tabela_classes,
        imprimir_resultado=imprimir_resultado,
        debug_string_ids=debug_string_ids,
        usar_main_input=usar_main_input,
        main_int_inputs_count=main_int_inputs_count,
        main_string_inputs=main_string_inputs,
    )
    programa_bril = gerador.gerar()
    caminho_saida = caminho.with_suffix(".bril.json")
    caminho_saida.write_text(
        json.dumps(programa_bril, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return caminho_saida


def main():
    argparser = argparse.ArgumentParser(
        description="Gera Bril JSON a partir de um programa COOL."
    )
    argparser.add_argument("arquivo", help="Arquivo .cl de entrada.")
    argparser.add_argument(
        "--no-print-main",
        action="store_true",
        help="Não imprime automaticamente resultado Int/Bool de Main.main.",
    )
    argparser.add_argument(
        "--strict-semantic",
        action="store_true",
        help="Interrompe se a análise semântica encontrar erro.",
    )
    argparser.add_argument(
        "--debug-string-ids",
        action="store_true",
        help="Para out_string não literal, imprime comprimento em vez de tentar imprimir chars.",
    )
    argparser.add_argument(
        "--main-input",
        action="store_true",
        help="Faz @main receber inteiros e usa esses valores em chamadas in_int().",
    )
    argparser.add_argument(
        "--main-inputs",
        type=int,
        default=None,
        help="Quantidade manual de inteiros que @main deve receber. Se omitido, conta in_int() em Main.main.",
    )
    argparser.add_argument(
        "--main-string",
        action="append",
        default=[],
        help="String fixa usada por in_string(); pode ser usada várias vezes.",
    )
    args = argparser.parse_args()
    try:
        caminho_saida = compilar_para_bril(
            args.arquivo,
            imprimir_resultado=not args.no_print_main,
            strict_semantic=args.strict_semantic,
            debug_string_ids=args.debug_string_ids,
            usar_main_input=args.main_input or args.main_inputs is not None,
            main_int_inputs_count=args.main_inputs,
            main_string_inputs=args.main_string,
        )
        print(f"Código Bril JSON gerado em: {caminho_saida}")
    except CodegenError as erro:
        print("Erro na geração de código Bril:")
        print(erro)
        sys.exit(1)


if __name__ == "__main__":
    main()
