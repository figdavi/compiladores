# Plan: Cool → Bril Code Generator

## Context

The Cool compiler pipeline (lex → parse → semantic) is complete and produces `.sem.json` files containing the fully type-annotated AST and class table. The next step is a code generator: `codegen_cool.py` that reads `.sem.json` and emits Bril IR as JSON (`.bril.json`).

Bril is a flat IR: it supports typed functions, `int`/`bool` values, arithmetic, comparison, control flow (`br`/`jmp`), and function calls. It has no concept of objects, strings, or classes.

**Strategy**: Simplified flat subset — each Cool class method becomes a standalone Bril function, `self` is threaded as an `int` argument, string operations are no-ops, and `new T` emits `const 0`. Only programs with no semantic errors are code-generated.

**Input**: `.sem.json` (output of `semantic_cool.py`)  
**Output**: `.bril.json` (Bril JSON format)

**Reference implementation**: `chocopy2bril/myast.py` — shows how to traverse an AST and emit Bril instructions.

---

## Type Mapping

| Cool type | Bril type | Notes |
|-----------|-----------|-------|
| `Int` | `int` | direct |
| `Bool` | `bool` | direct |
| `String` | — | unsupported; emit `const 0 : int` placeholder |
| `Object`, `SELF_TYPE`, class refs | `int` | simplified pointer/ID |

---

## Function Naming Convention

- `ClassName.methodName` → Bril function `@ClassName_methodName`
- `Main.main` → Bril function `@main` (no `self` arg, no return type — Bril entry point)
- Every other method gets `self: int` as first argument

---

## Task List

### Phase 0 — Setup

**Task 0.1 — Create a valid Cool test file**  
Create `testes_codegen/factorial.cl`: a `Main` class with `factorial(n: Int): Int` (using a while loop or if/recursion) and `main()` that calls `out_int(factorial(5))`. No strings except `out_int`.

Run `python semantic_cool.py testes_codegen/factorial.cl` to confirm zero semantic errors.

See `cool_testes_semanticos/validos/` for examples of valid Cool programs.

---

### Phase 1 — Core Infrastructure

**Task 1.1 — Create `codegen_cool.py` skeleton**  
Create `/home/figdavi/Desktop/compiladores/codegen_cool.py` with this structure:

```python
import json, sys

def cool_type_to_bril(t):
    if t == "Int": return "int"
    if t == "Bool": return "bool"
    return "int"   # everything else (objects, SELF_TYPE) simplified to int

class CodeGenerator:
    def __init__(self, sem_data):
        self.ast = sem_data["ast"]
        self.tabela_classes = sem_data["tabela_classes"]
        self.current_class = None   # str: class name being compiled
        self._reg = 0
        self._label = 0
        self.instrs = []            # list of Bril instruction dicts (current function)
        self.scope = {}             # var name → bril type

    def fresh_reg(self): ...
    def fresh_label(self, prefix): ...
    def reset_function_state(self): ...

    def generate(self): ...           # returns {"functions": [...]}
    def gen_program(self, node): ...
    def gen_class(self, node): ...
    def gen_method(self, class_name, node): ...
    def gen_expr(self, node): ...     # appends to self.instrs, returns dest reg name

if __name__ == "__main__":
    ...
```

**Task 1.2 — Implement helpers**  
In the same file:
- `fresh_reg()` → returns `_t0`, `_t1`, ... (uses and increments `self._reg`)
- `fresh_label(prefix)` → returns `prefix_0`, `prefix_1`, ... (uses and increments `self._label`)
- `reset_function_state()` → resets `_reg`, `_label`, `instrs`, `scope` to initial values

---

### Phase 2 — Expression Code Generation

All tasks in this phase implement `gen_expr(self, node)`. The method appends instructions to `self.instrs` and returns the name of the register holding the result.

**Task 2.1 — Literals and identifiers**  
Handle nodes by `node["no"]`:
- `"Inteiro"` → emit `{"op":"const","dest":t,"type":"int","value": node["valor"]}`, return `t`
- `"Booleano"` → emit `const` with value `1`/`0`, type `"bool"`
- `"Identificador"` where `node["nome"] == "self"` → return `"self"` directly (no instruction)
- `"Identificador"` otherwise → emit `{"op":"id","dest":t,"type": scope[name],"args":[name]}`, return `t`
- `"StringLiteral"` → emit `const 0 int`, return `t` (placeholder, strings unsupported)

**Task 2.2 — Binary and unary operators**  
- `"Binario"`: generate left → `l`, right → `r`; operator map: `+`→`add`, `-`→`sub`, `*`→`mul`, `/`→`div`, `<`→`lt`, `<=`→`le`, `=`→`eq`; result type: `"int"` for arithmetic, `"bool"` for comparisons
- `"NegacaoAritmetica"` (`~x`): generate x → `r`; emit `const 0`, then `sub zero_reg r` → return result
- `"Not"`: generate expr → `r`; emit `{"op":"not","dest":t,"type":"bool","args":[r]}`
- `"Isvoid"`: emit `{"op":"const","dest":t,"type":"bool","value":0}` (simplified: nothing is void)

**Task 2.3 — Block and assignment**  
- `"Bloco"`: generate each expr in `node["expressoes"]` in order; return the last dest
- `"Atribuicao"`: generate `node["valor"]` → `v`; emit `{"op":"id","dest": node["alvo"],"type": scope[alvo],"args":[v]}`; return `v`

**Task 2.4 — Let bindings**  
For `"Let"`:
- For each `DeclaracaoLet` in `node["declaracoes"]`:
  - Map `decl["tipo"]` to bril type `btype`
  - If `decl["inicializacao"]` is not null: generate it → `init_reg`; emit `id decl["nome"] ← init_reg`
  - Else: emit default `const 0` (or `const false` for Bool)
  - Add `decl["nome"] → btype` to `self.scope`
- Generate `node["corpo"]` → return its result

---

### Phase 3 — Control Flow

**Task 3.1 — If/else**  
For `"If"` node (`condicao`, `entao`, `senao`):
```
generate condicao → cond_reg
result = fresh_reg()
emit br cond_reg .then_N .else_N
emit label .then_N
generate entao → then_reg
emit id result ← then_reg
emit jmp .endif_N
emit label .else_N
generate senao → else_reg
emit id result ← else_reg
emit label .endif_N
return result
```

**Task 3.2 — While loop**  
For `"While"` node (`condicao`, `corpo`):
```
emit label .while_entry_N
generate condicao → cond_reg
emit br cond_reg .while_body_N .while_exit_N
emit label .while_body_N
generate corpo → _
emit jmp .while_entry_N
emit label .while_exit_N
emit const 0 int → t     # while returns Object (simplified as 0)
return t
```

---

### Phase 4 — Method Dispatch

**Task 4.1 — Simple call (implicit self)**  
For `"ChamadaSimples"` (`metodo`, `argumentos`):
- Check for built-ins first (Task 4.3)
- Look up `metodo` in `self.tabela_classes[self.current_class]["metodos"]` to get return type
- Generate each argument → list of regs
- Emit `{"op":"call","funcs":["@{current_class}_{metodo}"],"args":["self", arg1, ...],"dest":t,"type": bril_return_type}`
- Return `t`

**Task 4.2 — Dynamic and static dispatch**  
For `"Dispatch"` (`alvo`, `metodo`, `argumentos`):
- Generate `alvo` → `obj_reg`
- Use `alvo["tipo_inferido"]` as the dispatch class name
- Check for built-ins first (Task 4.3)
- Look up method return type in class table (walk inheritance chain if needed)
- Emit `call @ClassName_metodo obj_reg arg1 ...`

For `"DispatchEstatico"` (`alvo`, `tipo_estatico`, `metodo`, `argumentos`):
- Same as Dispatch but use `tipo_estatico` instead of `tipo_inferido`

**Task 4.3 — Built-in IO methods**  
Intercept before generic `call` emission when the method belongs to `IO` or `Object`:
- `out_int(x)`: emit `{"op":"print","args":[x_reg]}`; return `x_reg`
- `out_string(s)`: no-op; emit `const 0 int`, return it
- `in_int()`: emit `{"op":"read","dest":t,"type":"int"}`; return `t`
- `in_string()`: emit `const 0 int`, return it
- `abort()`: emit `{"op":"ret"}`; return `"_dummy"`

---

### Phase 5 — Object Creation & Case

**Task 5.1 — New expression**  
For `"New"` (`tipo`):
- Emit `{"op":"const","dest":t,"type":"int","value":0}`
- Return `t` (simplified: no heap allocation)

**Task 5.2 — Case expression (simplified)**  
For `"Case"` (`expressao`, `ramos`):
- Generate `expressao` → `expr_reg`
- Pick the branch whose `tipo` best matches `expressao["tipo_inferido"]` (exact match first, else first branch)
- Emit `id ramo["nome"] ← expr_reg` and add to scope
- Generate `ramo["expressao"]`, return result
- (Simplified: no runtime type check — purely static)

---

### Phase 6 — Integration

**Task 6.1 — Implement gen_program, gen_class, gen_method**  
Wire up top-level traversal:
- `gen_program`: iterate `node["classes"]`; skip classes where `tabela_classes[name]["basica"] == True`; call `gen_class` for each user class
- `gen_class`: iterate `node["features"]`; for each feature with `node["no"] == "Metodo"`, call `gen_method(class_name, feature)`
- `gen_method(class_name, node)`:
  - `reset_function_state()`
  - Set `self.current_class = class_name`
  - Build arg list: `[{"name":"self","type":"int"}]` + each formal mapped via `cool_type_to_bril`
  - Add all args to `self.scope`
  - Generate body: `gen_expr(node["corpo"])` → `last_reg`
  - Append `{"op":"ret","args":[last_reg]}` to `self.instrs`
  - Return function dict: `{"name": "ClassName_methodName", "args": [...], "type": bril_return_type, "instrs": self.instrs}`
- Special case `Main.main`: name as `"main"`, omit `self` arg, omit `"type"` key

**Task 6.2 — Entry point and file output**  
Complete `if __name__ == "__main__"`:
```python
path = sys.argv[1]
with open(path) as f:
    data = json.load(f)
if data["status"] not in ("sucesso",):
    print(f"Erros encontrados: {data['status']}", file=sys.stderr)
    sys.exit(1)
cg = CodeGenerator(data)
bril = cg.generate()
out_path = path.replace(".sem.json", ".bril.json")
with open(out_path, "w") as f:
    json.dump(bril, f, indent=2)
print(f"Bril gerado: {out_path}")
```

---

### Phase 7 — Testing

**Task 7.1 — End-to-end test**  
```bash
python semantic_cool.py testes_codegen/factorial.cl
python codegen_cool.py testes_codegen/factorial.sem.json
cat testes_codegen/factorial.bril.json
# Optional — if bril tools are installed:
brili < testes_codegen/factorial.bril.json
```
Expected: `@main` function present, output `120` (5!).

**Task 7.2 — Test with an existing valid program**  
Run one of `cool_testes_semanticos/validos/*.cl` through the full pipeline. Verify the Bril JSON is well-formed.

---

## Dependency Order

```
0.1 (test file)  ←  independent, do early

1.1 → 1.2
         └→ 2.1 ┐
         └→ 2.2 ├→ all feed into 6.1 → 6.2 → 7.1 → 7.2
         └→ 2.3 │
         └→ 2.4 │
         └→ 3.1 │
         └→ 3.2 │
         └→ 4.1 │
         └→ 4.2 │
         └→ 4.3 │
         └→ 5.1 │
         └→ 5.2 ┘
```

Phases 2–5 can be developed in parallel (they all add cases to `gen_expr`). Coordinate to avoid merge conflicts, or assign different `node["no"]` values to different people.

---

## Key Files to Read Before Starting

| File | Why |
|------|-----|
| `parser_cool.py` | All AST node `"no"` field values and their fields |
| `semantic_cool.py` | `ClasseInfo`, `MetodoInfo` structure |
| `exemplo.sem.json` | Real semantic output format (tabela_classes + AST) |
| `chocopy2bril/myast.py` | Reference Bril emission patterns |
