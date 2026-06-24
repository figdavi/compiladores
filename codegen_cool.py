import json, sys
from pathlib import Path
from lexer_cool import Lexer
from parser_cool import ParserCool, ErroSintatico
from semantic_cool import SemanticAnalyzer


def cool_type_to_bril(t):
    if t == "Int": return "int"
    if t == "Bool": return "bool"
    return "int"   # objects, SELF_TYPE, class refs → int handle


BUILTIN_CLASSES = {"IO", "Object"}
PTR_INT = {"ptr": "int"}   # Bril type for raw object pointer (internal use only)


class CodeGenerator:
    def __init__(self, sem_data):
        self.ast = sem_data["ast"]
        self.tabela_classes = sem_data["tabela_classes"]
        self.current_class = None
        self._reg = 0
        self._label = 0
        self.instrs = []
        self.scope = {}

        # OOP support tables
        self.class_tags = {}     # class_name → int tag (0,1,2,…)
        self.attr_layouts = {}   # class_name → {attr_name: slot_index}  (slot 0 = tag)
        self.class_sizes = {}    # class_name → total int slots
        self.class_children = {} # class_name → [direct child names]
        self._build_class_info()

    # ------------------------------------------------------------------ helpers

    def fresh_reg(self):
        t = f"_t{self._reg}"
        self._reg += 1
        return t

    def fresh_label(self, prefix):
        l = f"{prefix}_{self._label}"
        self._label += 1
        return l

    def reset_function_state(self):
        self._reg = 0
        self._label = 0
        self.instrs = []
        self.scope = {}

    # ------------------------------------------------------------------ class info

    def _build_class_info(self):
        tag = 0
        for cls in self.ast["classes"]:
            name = cls["nome"]
            if not self.tabela_classes.get(name, {}).get("basica", False):
                self.class_tags[name] = tag
                tag += 1

        for class_name, class_info in self.tabela_classes.items():
            if class_info.get("basica", False):
                continue
            attrs = self._collect_attrs(class_name)
            layout = {a: i + 1 for i, a in enumerate(attrs)}
            self.attr_layouts[class_name] = layout
            self.class_sizes[class_name] = 1 + len(attrs)
            if class_name not in self.class_children:
                self.class_children[class_name] = []
            pai = class_info.get("pai")
            if pai and not self.tabela_classes.get(pai, {}).get("basica", False):
                self.class_children.setdefault(pai, []).append(class_name)

    def _collect_attrs(self, class_name):
        """Return ordered list of attr names for class_name, parents first."""
        cls_info = self.tabela_classes.get(class_name, {})
        pai = cls_info.get("pai")
        parent_attrs = []
        if pai and not self.tabela_classes.get(pai, {}).get("basica", False):
            parent_attrs = self._collect_attrs(pai)
        own = list(cls_info.get("atributos", {}).keys())
        seen = set(parent_attrs)
        return parent_attrs + [a for a in own if a not in seen]

    def _find_defining_class(self, class_name, method_name):
        """Walk MRO upward to find the class that defines method_name."""
        current = class_name
        while current:
            cls = self.tabela_classes.get(current, {})
            if method_name in cls.get("metodos", {}):
                return current
            current = cls.get("pai")
        return None

    def _all_concrete_subclasses(self, class_name):
        """All non-abstract descendants (those with a class tag)."""
        result = []
        if class_name in self.class_tags:
            result.append(class_name)
        for child in self.class_children.get(class_name, []):
            result.extend(self._all_concrete_subclasses(child))
        return result

    def _methods_overridden_in_subclasses(self, class_name):
        """Set of method names overridden in any (direct/indirect) subclass."""
        overridden = set()
        for child in self.class_children.get(class_name, []):
            child_info = self.tabela_classes.get(child, {})
            overridden |= set(child_info.get("metodos", {}).keys())
            overridden |= self._methods_overridden_in_subclasses(child)
        return overridden

    # ------------------------------------------------------------------ method lookup

    def find_method(self, class_name, method_name):
        current = class_name
        while current:
            cls = self.tabela_classes.get(current, {})
            if method_name in cls.get("metodos", {}):
                return cls["metodos"][method_name]
            current = cls.get("pai")
        return None

    def is_builtin_method(self, method_info):
        return method_info is not None and method_info.get("classe") in BUILTIN_CLASSES

    def emit_builtin(self, method_name, arg_regs):
        if method_name == "out_int":
            if arg_regs:
                self.instrs.append({"op": "print", "args": arg_regs})
                return arg_regs[0]
            t = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": t, "type": "int", "value": 0})
            return t
        elif method_name == "in_int":
            t = self.fresh_reg()
            self.instrs.append({"op": "read", "dest": t, "type": "int"})
            return t
        else:
            t = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": t, "type": "int", "value": 0})
            return t

    # ------------------------------------------------------------------ object memory helpers

    def _emit_new(self, class_name):
        """Allocate a new object, set tag, zero-init attrs. Returns int handle reg."""
        size = self.class_sizes.get(class_name, 1)
        tag_val = self.class_tags.get(class_name, 0)

        size_t = self.fresh_reg()
        ptr_t = self.fresh_reg()
        self.instrs.append({"op": "const", "dest": size_t, "type": "int", "value": size})
        self.instrs.append({"op": "alloc", "dest": ptr_t, "type": PTR_INT, "args": [size_t]})

        tag_t = self.fresh_reg()
        self.instrs.append({"op": "const", "dest": tag_t, "type": "int", "value": tag_val})
        self.instrs.append({"op": "store", "args": [ptr_t, tag_t]})

        for slot in range(1, size):
            off_t = self.fresh_reg()
            sp_t = self.fresh_reg()
            z_t = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": off_t, "type": "int", "value": slot})
            self.instrs.append({"op": "ptradd", "dest": sp_t, "type": PTR_INT, "args": [ptr_t, off_t]})
            self.instrs.append({"op": "const", "dest": z_t, "type": "int", "value": 0})
            self.instrs.append({"op": "store", "args": [sp_t, z_t]})

        handle_t = self.fresh_reg()
        self.instrs.append({"op": "ptrToInt", "dest": handle_t, "type": "int", "args": [ptr_t]})
        return handle_t

    def _emit_attr_load(self, obj_handle_reg, slot):
        """Load int slot from object. Returns register with value."""
        ptr_t = self.fresh_reg()
        off_t = self.fresh_reg()
        sp_t = self.fresh_reg()
        val_t = self.fresh_reg()
        self.instrs.append({"op": "intToPtr", "dest": ptr_t, "type": PTR_INT, "args": [obj_handle_reg]})
        self.instrs.append({"op": "const", "dest": off_t, "type": "int", "value": slot})
        self.instrs.append({"op": "ptradd", "dest": sp_t, "type": PTR_INT, "args": [ptr_t, off_t]})
        self.instrs.append({"op": "load", "dest": val_t, "type": "int", "args": [sp_t]})
        return val_t

    def _emit_attr_store(self, obj_handle_reg, slot, val_reg):
        """Store int val_reg into object slot."""
        ptr_t = self.fresh_reg()
        off_t = self.fresh_reg()
        sp_t = self.fresh_reg()
        self.instrs.append({"op": "intToPtr", "dest": ptr_t, "type": PTR_INT, "args": [obj_handle_reg]})
        self.instrs.append({"op": "const", "dest": off_t, "type": "int", "value": slot})
        self.instrs.append({"op": "ptradd", "dest": sp_t, "type": PTR_INT, "args": [ptr_t, off_t]})
        self.instrs.append({"op": "store", "args": [sp_t, val_reg]})

    # ------------------------------------------------------------------ expression codegen

    def gen_expr(self, node):
        kind = node["no"]

        if kind == "Inteiro":
            t = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": t, "type": "int", "value": node["valor"]})
            return t

        elif kind == "Booleano":
            t = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": t, "type": "bool", "value": node["valor"]})
            return t

        elif kind == "Identificador":
            nome = node["nome"]
            if nome == "self":
                return "self"
            # class attribute → load from object memory
            slot = self.attr_layouts.get(self.current_class, {}).get(nome)
            if slot is not None and nome not in self.scope:
                return self._emit_attr_load("self", slot)
            btype = self.scope.get(nome, "int")
            t = self.fresh_reg()
            self.instrs.append({"op": "id", "dest": t, "type": btype, "args": [nome]})
            return t

        elif kind == "StringLiteral":
            t = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": t, "type": "int", "value": 0})
            return t

        elif kind == "Binario":
            l = self.gen_expr(node["esquerda"])
            r = self.gen_expr(node["direita"])
            op_map = {
                "+":  ("add", "int"),
                "-":  ("sub", "int"),
                "*":  ("mul", "int"),
                "/":  ("div", "int"),
                "<":  ("lt",  "bool"),
                "<=": ("le",  "bool"),
                "=":  ("eq",  "bool"),
            }
            bril_op, result_type = op_map.get(node["operador"], ("add", "int"))
            t = self.fresh_reg()
            self.instrs.append({"op": bril_op, "dest": t, "type": result_type, "args": [l, r]})
            return t

        elif kind == "NegacaoAritmetica":
            r = self.gen_expr(node["expressao"])
            zero = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": zero, "type": "int", "value": 0})
            t = self.fresh_reg()
            self.instrs.append({"op": "sub", "dest": t, "type": "int", "args": [zero, r]})
            return t

        elif kind == "Not":
            r = self.gen_expr(node["expressao"])
            t = self.fresh_reg()
            self.instrs.append({"op": "not", "dest": t, "type": "bool", "args": [r]})
            return t

        elif kind == "Isvoid":
            t = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": t, "type": "bool", "value": False})
            return t

        elif kind == "Bloco":
            last = None
            for expr in node["expressoes"]:
                last = self.gen_expr(expr)
            return last

        elif kind == "Atribuicao":
            alvo = node["nome"]
            v = self.gen_expr(node["valor"])
            slot = self.attr_layouts.get(self.current_class, {}).get(alvo)
            if slot is not None and alvo not in self.scope:
                # store into object memory; the assignment expression returns the value
                # store as int (bools become 0/1 at use site)
                store_reg = v
                if self.scope.get(alvo) == "bool" or cool_type_to_bril(node.get("tipo_inferido","Object")) == "bool":
                    # cast bool to int for storage
                    int_t = self.fresh_reg()
                    zero_t = self.fresh_reg()
                    self.instrs.append({"op": "const", "dest": zero_t, "type": "int", "value": 0})
                    # use select-like trick: not needed for simple bool→int, just id as int is wrong
                    # simplest: store the bool reg directly (brili won't type-check store)
                    store_reg = v
                self._emit_attr_store("self", slot, store_reg)
                return v
            btype = self.scope.get(alvo, cool_type_to_bril(node.get("tipo_inferido", "Object")))
            self.instrs.append({"op": "id", "dest": alvo, "type": btype, "args": [v]})
            return v

        elif kind == "Let":
            for decl in node["declaracoes"]:
                btype = cool_type_to_bril(decl["tipo"])
                nome = decl["nome"]
                if decl["inicializacao"] is not None:
                    init_reg = self.gen_expr(decl["inicializacao"])
                    self.instrs.append({"op": "id", "dest": nome, "type": btype, "args": [init_reg]})
                elif btype == "bool":
                    self.instrs.append({"op": "const", "dest": nome, "type": "bool", "value": False})
                else:
                    self.instrs.append({"op": "const", "dest": nome, "type": "int", "value": 0})
                self.scope[nome] = btype
            return self.gen_expr(node["corpo"])

        elif kind == "If":
            n = self._label
            self._label += 1
            then_label  = f"then_{n}"
            else_label  = f"else_{n}"
            endif_label = f"endif_{n}"

            cond_reg = self.gen_expr(node["condicao"])
            result_type = cool_type_to_bril(node.get("tipo_inferido", "Object"))
            result = self.fresh_reg()

            self.instrs.append({"op": "br", "args": [cond_reg], "labels": [then_label, else_label]})
            self.instrs.append({"label": then_label})
            then_reg = self.gen_expr(node["entao"])
            self.instrs.append({"op": "id", "dest": result, "type": result_type, "args": [then_reg]})
            self.instrs.append({"op": "jmp", "labels": [endif_label]})
            self.instrs.append({"label": else_label})
            else_reg = self.gen_expr(node["senao"])
            self.instrs.append({"op": "id", "dest": result, "type": result_type, "args": [else_reg]})
            self.instrs.append({"label": endif_label})
            return result

        elif kind == "While":
            n = self._label
            self._label += 1
            entry_label = f"while_entry_{n}"
            body_label  = f"while_body_{n}"
            exit_label  = f"while_exit_{n}"

            self.instrs.append({"label": entry_label})
            cond_reg = self.gen_expr(node["condicao"])
            self.instrs.append({"op": "br", "args": [cond_reg], "labels": [body_label, exit_label]})
            self.instrs.append({"label": body_label})
            self.gen_expr(node["corpo"])
            self.instrs.append({"op": "jmp", "labels": [entry_label]})
            self.instrs.append({"label": exit_label})
            t = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": t, "type": "int", "value": 0})
            return t

        elif kind == "ChamadaSimples":
            metodo = node["metodo"]
            arg_regs = [self.gen_expr(a) for a in node["argumentos"]]

            method_info = self.find_method(self.current_class, metodo)
            if self.is_builtin_method(method_info):
                return self.emit_builtin(metodo, arg_regs)

            ret_type = cool_type_to_bril(method_info["tipo_retorno"]) if method_info else "int"
            t = self.fresh_reg()
            self.instrs.append({
                "op": "call",
                "funcs": [f"@{self.current_class}_{metodo}"],
                "args": ["self"] + arg_regs,
                "dest": t,
                "type": ret_type,
            })
            return t

        elif kind == "Dispatch":
            alvo_node = node["alvo"]
            metodo = node["metodo"]
            obj_reg = self.gen_expr(alvo_node)
            arg_regs = [self.gen_expr(a) for a in node["argumentos"]]

            dispatch_class = alvo_node.get("tipo_inferido", "Object")
            if dispatch_class == "SELF_TYPE":
                dispatch_class = self.current_class

            method_info = self.find_method(dispatch_class, metodo)
            if self.is_builtin_method(method_info):
                return self.emit_builtin(metodo, arg_regs)

            ret_type = cool_type_to_bril(method_info["tipo_retorno"]) if method_info else "int"
            t = self.fresh_reg()
            self.instrs.append({
                "op": "call",
                "funcs": [f"@{dispatch_class}_{metodo}"],
                "args": [obj_reg] + arg_regs,
                "dest": t,
                "type": ret_type,
            })
            return t

        elif kind == "DispatchEstatico":
            alvo_node = node["alvo"]
            tipo_estatico = node["tipo_estatico"]
            metodo = node["metodo"]
            obj_reg = self.gen_expr(alvo_node)
            arg_regs = [self.gen_expr(a) for a in node["argumentos"]]

            method_info = self.find_method(tipo_estatico, metodo)
            if self.is_builtin_method(method_info):
                return self.emit_builtin(metodo, arg_regs)

            ret_type = cool_type_to_bril(method_info["tipo_retorno"]) if method_info else "int"
            t = self.fresh_reg()
            self.instrs.append({
                "op": "call",
                "funcs": [f"@{tipo_estatico}_{metodo}"],
                "args": [obj_reg] + arg_regs,
                "dest": t,
                "type": ret_type,
            })
            return t

        elif kind == "New":
            tipo = node["tipo"]
            if tipo in BUILTIN_CLASSES or self.tabela_classes.get(tipo, {}).get("basica", False):
                t = self.fresh_reg()
                self.instrs.append({"op": "const", "dest": t, "type": "int", "value": 0})
                return t
            return self._emit_new(tipo)

        elif kind == "Case":
            expr_reg = self.gen_expr(node["expressao"])
            expr_type = node["expressao"].get("tipo_inferido", "Object")

            ramos = node["ramos"]
            chosen = next((r for r in ramos if r["tipo"] == expr_type), None)
            if chosen is None and ramos:
                chosen = ramos[0]

            if chosen:
                nome = chosen["nome"]
                btype = cool_type_to_bril(chosen["tipo"])
                self.scope[nome] = btype
                self.instrs.append({"op": "id", "dest": nome, "type": btype, "args": [expr_reg]})
                return self.gen_expr(chosen["expressao"])

            t = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": t, "type": "int", "value": 0})
            return t

        else:
            t = self.fresh_reg()
            self.instrs.append({"op": "const", "dest": t, "type": "int", "value": 0})
            return t

    # ------------------------------------------------------------------ method / class codegen

    def gen_method(self, class_name, node):
        self.reset_function_state()
        self.current_class = class_name

        is_main = (class_name == "Main" and node["nome"] == "main")

        args = []
        if is_main:
            # Allocate a real Main object so attribute access (e.g. l : List) works
            handle = self._emit_new("Main")
            self.instrs.append({"op": "id", "dest": "self", "type": "int", "args": [handle]})
            self.scope["self"] = "int"
        else:
            args.append({"name": "self", "type": "int"})
            self.scope["self"] = "int"

        for formal in node["formais"]:
            btype = cool_type_to_bril(formal["tipo"])
            args.append({"name": formal["nome"], "type": btype})
            self.scope[formal["nome"]] = btype

        # attrs are now in object memory — do NOT init as locals
        # (scope only has method formals + let bindings)

        last_reg = self.gen_expr(node["corpo"])

        if is_main:
            self.instrs.append({"op": "ret"})
            return {"name": "main", "args": [], "instrs": list(self.instrs)}
        else:
            ret_type = cool_type_to_bril(node["tipo_retorno"])
            body_bril_type = cool_type_to_bril(node["corpo"].get("tipo_inferido", node["tipo_retorno"]))
            if ret_type != body_bril_type:
                # Type mismatch (e.g. body returns Bool but method declares Object→int)
                coerced = self.fresh_reg()
                if ret_type == "int":
                    self.instrs.append({"op": "const", "dest": coerced, "type": "int", "value": 0})
                else:
                    self.instrs.append({"op": "const", "dest": coerced, "type": "bool", "value": False})
                last_reg = coerced
            self.instrs.append({"op": "ret", "args": [last_reg]})
            return {
                "name": f"{class_name}_{node['nome']}",
                "args": args,
                "type": ret_type,
                "instrs": list(self.instrs),
            }

    def gen_dispatch_wrapper(self, abstract_class, method_name):
        """Generate @abstract_class_method_name as a tag-based dispatch wrapper."""
        self.reset_function_state()
        self.current_class = abstract_class

        method_info = self.find_method(abstract_class, method_name)
        if method_info is None:
            return None

        ret_type = cool_type_to_bril(method_info["tipo_retorno"])
        formals = method_info.get("formais", [])

        args = [{"name": "self", "type": "int"}]
        for f in formals:
            args.append({"name": f["nome"], "type": cool_type_to_bril(f["tipo"])})
        call_args = ["self"] + [f["nome"] for f in formals]

        # Load runtime tag from self
        ptr_t = self.fresh_reg()
        tag_t = self.fresh_reg()
        self.instrs.append({"op": "intToPtr", "dest": ptr_t, "type": PTR_INT, "args": ["self"]})
        self.instrs.append({"op": "load", "dest": tag_t, "type": "int", "args": [ptr_t]})

        result_t = self.fresh_reg()
        done_label = self.fresh_label("dispatch_done")

        concrete = self._all_concrete_subclasses(abstract_class)
        for child in concrete:
            tag_val = self.class_tags[child]
            defining = self._find_defining_class(child, method_name)
            # Skip if not overridden in this subclass (would recurse back to this wrapper)
            if defining is None or defining == abstract_class:
                continue

            match_t = self.fresh_reg()
            tag_val_t = self.fresh_reg()
            hit_label = self.fresh_label(f"dispatch_{child}")
            miss_label = self.fresh_label(f"miss_{child}")

            self.instrs.append({"op": "const", "dest": tag_val_t, "type": "int", "value": tag_val})
            self.instrs.append({"op": "eq", "dest": match_t, "type": "bool", "args": [tag_t, tag_val_t]})
            self.instrs.append({"op": "br", "args": [match_t], "labels": [hit_label, miss_label]})
            self.instrs.append({"label": hit_label})

            r = self.fresh_reg()
            self.instrs.append({
                "op": "call",
                "funcs": [f"@{defining}_{method_name}"],
                "args": call_args,
                "dest": r,
                "type": ret_type,
            })
            self.instrs.append({"op": "id", "dest": result_t, "type": ret_type, "args": [r]})
            self.instrs.append({"op": "jmp", "labels": [done_label]})
            self.instrs.append({"label": miss_label})

        # default fallback
        z = self.fresh_reg()
        if ret_type == "bool":
            self.instrs.append({"op": "const", "dest": z, "type": "bool", "value": False})
        else:
            self.instrs.append({"op": "const", "dest": z, "type": "int", "value": 0})
        self.instrs.append({"op": "id", "dest": result_t, "type": ret_type, "args": [z]})
        self.instrs.append({"label": done_label})
        self.instrs.append({"op": "ret", "args": [result_t]})

        return {
            "name": f"{abstract_class}_{method_name}",
            "args": args,
            "type": ret_type,
            "instrs": list(self.instrs),
        }

    def gen_class(self, node):
        funcs = []
        class_name = node["nome"]
        has_subclasses = bool(self.class_children.get(class_name))
        overridden = self._methods_overridden_in_subclasses(class_name) if has_subclasses else set()

        for feature in node["features"]:
            if feature["no"] != "Metodo":
                continue
            method_name = feature["nome"]
            if has_subclasses and method_name in overridden:
                # Skip Cool body — this slot will be a dispatch wrapper
                continue
            funcs.append(self.gen_method(class_name, feature))
        return funcs

    def gen_program(self, node):
        funcs = []

        # Concrete method implementations
        for cls in node["classes"]:
            if self.tabela_classes.get(cls["nome"], {}).get("basica", False):
                continue
            funcs.extend(self.gen_class(cls))

        # Dispatch wrappers for abstract classes
        for class_name in list(self.class_children.keys()):
            if not self.class_children[class_name]:
                continue  # no subclasses → no wrappers needed
            overridden = self._methods_overridden_in_subclasses(class_name)
            for method_name in overridden:
                w = self.gen_dispatch_wrapper(class_name, method_name)
                if w:
                    funcs.append(w)

        return funcs

    def generate(self):
        return {"functions": self.gen_program(self.ast)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 codegen_cool.py <arquivo.cl>", file=sys.stderr)
        sys.exit(1)

    caminho = sys.argv[1]
    codigo = Path(caminho).read_text(encoding="utf-8")

    lexer = Lexer(codigo)
    tokens = lexer.tokenizar()

    if lexer.erros:
        print(f"{len(lexer.erros)} erro(s) léxico(s):", file=sys.stderr)
        for e in lexer.erros:
            print(f"  Linha {e.linha}, Col {e.coluna}: {e.mensagem}", file=sys.stderr)
        sys.exit(1)

    try:
        parser = ParserCool(tokens)
        ast = parser.parse()
    except ErroSintatico as e:
        d = e.para_dict()
        print(f"Erro sintático — Linha {d['linha']}, Col {d['coluna']}: {d['mensagem']}", file=sys.stderr)
        sys.exit(1)

    analisador = SemanticAnalyzer(ast)
    resultado = analisador.analisar()

    if analisador.erros:
        print(f"{resultado['quantidade_erros_semanticos']} erro(s) semântico(s):", file=sys.stderr)
        for erro in resultado["erros_semanticos"]:
            print(f"  Linha {erro['linha']}, Col {erro['coluna']}: {erro['mensagem']}", file=sys.stderr)
        sys.exit(1)

    sem_data = {
        "status": "sucesso",
        "ast": ast,
        "tabela_classes": resultado["tabela_classes"],
    }
    cg = CodeGenerator(sem_data)
    bril = cg.generate()
    out_path = caminho.replace(".cl", ".bril.json")
    with open(out_path, "w") as f:
        json.dump(bril, f, indent=2)
    print(f"Bril gerado: {out_path}")
