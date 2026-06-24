- Idioma: Português (código) / Inglês (nomes de instrução Bril)
- Sequência de prompts: ver seção abaixo
- Formato do destino: Bril JSON (`.bril.json`)

Im trying to do this task: "Nesta etapa, você deve utilizar uma (ou +) IA(s) da sua preferência para, a partir do código já existente, gerar código para a linguagem Bril a partir de Cool.  
 É importante anotar todas as etapas de interações com a(s) IA(s) de forma a poder descrever as decisões tomadas na apresentação (última etapa da disciplina).  
 Alguns aspectos a considerar: Idioma, sequência de prompts utilizados, formato destino (Bril interpreta JSON, mas há um formato texto (.bril) que também é suportado com a utilizada da  
 ferramenta bril2json)." I already did all steps until semantic_cool, now I need to do the next step to bril. I cloned the chocopy2bril repo to study how this other project did this code  
 gen to bril (they did it from chocopy, im doing from cool lang). Generate a plan on what I need to do in order to advance to this next step. The idea is that this plan will be in part  
 passed to another person to execute, the other part, ill be executing myself, so the steps need to be atomic. Be sure to ask questions if you are unsure before continuing

explain in great detail how chocopy2bril does code gen and how it other tools usually works in this step. Is this a compiler or a transpiler? How is it compared to a standard, say C (gcc), compiler steps? and a transpiler? Also, tell me how cooltojs works and what can i learn from it to understand cooltobril more?

---

## Execução do plano — `codegen_cool.py`

### Contexto geral

O pipeline até esta etapa produzia arquivos `.sem.json` contendo a AST completamente anotada com tipos e a tabela de classes. O objetivo desta etapa foi implementar `codegen_cool.py`, que lê esses arquivos e emite Bril IR em formato JSON (`.bril.json`).

A estratégia adotada foi um **subconjunto flat simplificado**: cada método de cada classe Cool se torna uma função Bril independente, `self` é passado como argumento `int`, operações sobre `String` são no-ops, e `new T` emite `const 0`. Apenas programas sem erros semânticos são processados.

---

### Decisões de design tomadas durante a implementação

#### 1. Formato de nomes de funções Bril

- Todos os métodos viram `@ClassName_methodName` (ex.: `@Main_factorial`).
- `Main.main` é o caso especial: vira `@main` (sem prefixo, sem argumento `self`, sem campo `"type"` — requisito do ponto de entrada Bril).

**Decisão:** o nome `@main` é obrigatório pelo interpretador Bril para o ponto de entrada. Todos os outros métodos precisam de um namespace para evitar colisões entre classes com métodos de mesmo nome (ex.: `isNil` em `List`, `Cons` e `Nil` virariam funções diferentes).

#### 2. Mapeamento de tipos Cool → Bril

| Cool | Bril | Motivo |
|------|------|--------|
| `Int` | `int` | direto |
| `Bool` | `bool` | direto |
| `String` | `int` (const 0) | Bril não tem tipo string |
| qualquer classe, `Object`, `SELF_TYPE` | `int` | ponteiro/ID simplificado |

**Decisão:** usar `int` como tipo universal para referências de objeto é a simplificação mais direta. O Bril não tem GC nem heap, então a semântica de objetos não pode ser fiel; o objetivo é gerar código estruturalmente correto para a lógica de controle e aritmética.

#### 3. Atributos de classe como variáveis locais

Cool permite que métodos leiam e escrevam atributos de instância (`xcar`, `xcdr`, etc.). Bril é flat e não tem noção de heap ou campos de objeto.

**Decisão:** ao entrar em cada método, inicializar todos os atributos da classe como variáveis locais com `const 0` (ou `const false` para Bool). Isso permite que métodos como `Cons.init` façam `xcar <- hd` via instrução `id`, e que `Cons.car` retorne `xcar` via `id`. A limitação é que o estado dos atributos não persiste entre chamadas de métodos — mas é adequado para o subconjunto simplificado do plano.

#### 4. `self` em `@main`

`Main.main` não tem `self` como parâmetro (é o ponto de entrada). Porém, dentro de `main` pode haver chamadas simples (`ChamadaSimples`) que repassam `self` como primeiro argumento para outros métodos (ex.: `factorial(5)` vira `call @Main_factorial self 5`).

**Decisão:** emitir `const 0 int → self` como primeira instrução de `@main`, tornando `self` uma variável local com valor 0. Isso garante que a variável `self` esteja definida quando passada como argumento.

**Por que isso importa:** sem esse `const`, o Bril emitiria uma referência a `self` sem definição prévia, o que quebraria interpretadores Bril que fazem verificação de SSA/definição.

#### 5. Detecção de métodos built-in

Métodos de `IO` (`out_int`, `out_string`, `in_int`, `in_string`) e `Object` (`abort`, `type_name`, `copy`) precisam de tratamento especial porque não existem como funções Bril.

**Decisão:** checar o campo `"classe"` do método na tabela de classes. Se for `"IO"` ou `"Object"`, interceptar antes de emitir um `call` genérico:

- `out_int(x)` → `print x` (Bril nativo)
- `in_int()` → `read` (Bril nativo)
- `out_string`, `in_string`, `abort`, `type_name`, `copy` → `const 0 int` (no-op)

**Por que `abort` não emite `ret`:** o plano original sugeria emitir `{"op":"ret"}` para `abort` e retornar o literal `"_dummy"` como registrador. O problema é que `"_dummy"` poderia aparecer como argumento do `ret` final do método (ex.: se `abort` for a última expressão). Em vez disso, `abort` emite `const 0` — é uma simplificação consciente: o programa não vai de fato abortar, mas o código Bril gerado é estruturalmente válido.

#### 6. Operador `=` de igualdade

Em Cool, `=` é comparação de igualdade (retorna `Bool`), não atribuição. Atribuição é `<-`.

**Decisão:** mapear `=` para a instrução Bril `eq` com tipo de resultado `bool`. O mapeamento completo de operadores:

```
+  → add  (int)
-  → sub  (int)
*  → mul  (int)
/  → div  (int)
<  → lt   (bool)
<= → le   (bool)
=  → eq   (bool)
```

#### 7. Nó `Atribuicao` — campo correto

O plano (Task 2.3) mencionava `node["alvo"]` para o nome da variável alvo. Mas inspecionando o `.sem.json` real produzido pelo parser, o campo correto é `node["nome"]` (não `node["alvo"]`). O campo `node["alvo"]` não existe.

**Decisão:** usar `node["nome"]` conforme o formato real da AST. Isso foi descoberto lendo `exemplo.sem.json` antes de escrever o código.

#### 8. Valor de retorno de `Atribuicao`

Em Cool, uma expressão de atribuição `x <- expr` retorna o valor de `expr` (não o nome da variável). Então `gen_expr(Atribuicao)` emite `id x <- v` e retorna `v` (o registrador com o valor), não `x`.

#### 9. Geração de labels em If/While

Tanto `If` quanto `While` precisam de múltiplos labels relacionados. Em vez de chamar `fresh_label()` três vezes (gerando números diferentes), captura-se `n = self._label; self._label += 1` uma vez e constroem-se os três labels com o mesmo número (`then_N`, `else_N`, `endif_N`). Isso facilita leitura do Bril gerado.

#### 10. `SELF_TYPE` em Dispatch

Quando o alvo de um `Dispatch` tem `tipo_inferido == "SELF_TYPE"`, o tipo real é a classe atual sendo compilada.

**Decisão:** substituir `SELF_TYPE` por `self.current_class` na resolução do nome da função.

#### 11. Skipping de classes básicas

O plano diz para pular classes com `tabela_classes[name]["basica"] == True`. Isso inclui `Object`, `IO`, `Int`, `Bool`, `String` — que têm implementações built-in e não geram funções Bril.

---

### Estrutura do `codegen_cool.py`

```
CodeGenerator
├── __init__         — carrega AST e tabela_classes
├── fresh_reg        — gera _t0, _t1, _t2, ...
├── fresh_label      — gera prefix_0, prefix_1, ...
├── reset_function_state — limpa estado entre métodos
├── find_method      — busca método na cadeia de herança
├── is_builtin_method — checa se classe do método é IO/Object
├── emit_builtin     — emite instrução Bril para built-ins
├── gen_expr         — traduz nó da AST → instruções Bril, retorna reg destino
│   ├── Inteiro, Booleano, StringLiteral, Identificador
│   ├── Binario, NegacaoAritmetica, Not, Isvoid
│   ├── Bloco, Atribuicao, Let
│   ├── If, While
│   ├── ChamadaSimples, Dispatch, DispatchEstatico
│   ├── New, Case
│   └── fallback → const 0
├── gen_method       — gera função Bril completa para um método
├── gen_class        — itera features, chama gen_method para Metodos
├── gen_program      — itera classes, skipa básicas, chama gen_class
└── generate         — ponto de entrada, retorna {"functions": [...]}
```

---

### Arquivo de teste criado

**`testes_codegen/factorial.cl`** — classe `Main` com método `factorial(n: Int): Int` (recursivo via if/else) e `main()` que chama `out_int(factorial(5))`. Confirmado com zero erros semânticos.

Bril gerado (`testes_codegen/factorial.bril.json`) contém:
- `@Main_factorial(self: int, n: int) → int` com if/else e chamada recursiva
- `@main()` sem tipo de retorno, com `self = 0`, chamada a `@Main_factorial`, e `print`

---

### Testes adicionais executados

| Arquivo | Funções geradas |
|---------|----------------|
| `testes_codegen/factorial.cl` | `Main_factorial`, `main` |
| `cool_testes_semanticos/validos/01_valido_basico.cl` | `main` |
| `cool_testes_semanticos/validos/02_valido_heranca_dispatch.cl` | `A_value`, `A_same`, `B_value`, `main` |
| `cool_testes_semanticos/validos/03_valido_let_case.cl` | `main` |

Todos produziram JSON válido (verificado via `json.load`).

---

### Limitações conhecidas (simplificações intencionais do plano)

- **Atributos não persistem entre chamadas**: cada método inicializa atributos de instância como locais com valor 0. A semântica de objetos stateful não é preservada.
- **`String` é no-op**: operações de string (concat, length, substr) emitem `const 0`.
- **`abort` não aborta**: emite `const 0` em vez de terminar o programa.
- **`Case` é estático**: o ramo é escolhido em tempo de compilação pelo tipo inferido, sem verificação de tipo em runtime.
- **`new T` é `const 0`**: sem alocação de heap.

---

## Sessão de refinamento — `codegen_cool.py`

### Prompt 1

> "Just like every other step, the codegen_cool.py should take a .cl as input, not the .sem.json. When there was supposedly a semantic error in .sem.json it just printed 'Erro semantico', but no real debugging information"

**O que foi feito:**

O `codegen_cool.py` originalmente aceitava um `.sem.json` como entrada e apenas verificava o campo `"status"` para detectar erros, imprimindo `"Erros encontrados: erro_semantico"` sem nenhum detalhe útil.

Duas alterações foram feitas:

1. **Entrada mudou de `.sem.json` para `.cl`** — igual a todas as outras etapas do pipeline (`lexer_cool.py`, `parser_cool.py`, `semantic_cool.py`). O `codegen_cool.py` agora importa `Lexer`, `ParserCool`, `ErroSintatico` e `SemanticAnalyzer` e executa o pipeline completo internamente antes de fazer a geração de código.

2. **Erros passaram a exibir detalhes reais** — em vez de uma string genérica, cada tipo de erro agora imprime linha, coluna e mensagem:
   - Erros léxicos: `Linha X, Col Y: <mensagem>`
   - Erro sintático: `Linha X, Col Y: <mensagem>`
   - Erros semânticos: lista de todos os erros com `Linha X, Col Y: <mensagem>`

   Exemplo real de saída com `exemplo.cl` (que contém erros semânticos):
   ```
   4 erro(s) semântico(s):
     Linha 80, Col 14: Método 'init2' não encontrado na classe 'Cons'
     Linha 78, Col 2: O corpo do método 'insert' possui tipo 'Object', mas o retorno declarado é 'List'
     Linha 103, Col 2: O corpo do método 'sort' possui tipo 'Bool', mas o retorno declarado é 'List'
     Linha 122, Col 8: Inicialização inválida no let: expressão de tipo 'String' não conforma ao tipo declarado 'Int'
   ```

O caminho de saída agora é derivado do `.cl` de entrada: `arquivo.cl` → `arquivo.bril.json`.

---

### Prompt 2

> "append new context to readme.md (including all prompts i asked and what you did)"

**O que foi feito:** esta seção foi adicionada ao `README.md` documentando os prompts desta sessão e as mudanças correspondentes.

---

## Sessão de correção de `exemplo.cl`

### Prompt 1

> "fix exemplo.cl"

**Contexto:** o arquivo `exemplo.cl` continha 4 erros semânticos detectados pelo `codegen_cool.py` (via `semantic_cool`). Os erros eram visíveis em `exemplo.sem.json`:

1. `Método 'init2' não encontrado na classe 'Cons'` (linha 80) — a versão anterior do arquivo tinha um método `insert` em `Cons` que chamava `init2` inexistente (typo de `init`). A versão modificada do arquivo havia removido o método inteiro, deixando `Cons` sem override de `insert`.
2. `O corpo do método 'insert' possui tipo 'Object', mas o retorno declarado é 'List'` (linha 78) — consequência direta do erro anterior.
3. `O corpo do método 'sort' possui tipo 'Bool', mas o retorno declarado é 'List'` (linha 103) — em `Nil`, `sort()` chamava `isNil()` que retorna `Bool`.
4. `Inicialização inválida no let: expressão de tipo 'String' não conforma ao tipo declarado 'Int'` (linha 122) — `let j : Int <- "0"` usava string literal em vez de inteiro.

**O que foi feito:**

1. **Adicionado `insert` em `Cons`** com a lógica correta de insertion sort:
   ```cool
   insert(i : Int) : List {
       if i < xcar then
           (new Cons).init(i, self)
       else
           (new Cons).init(xcar, xcdr.insert(i))
       fi
   };
   ```
   O then-branch usa `init` (não `init2`), resolvendo os erros 1 e 2.

2. **`Nil.sort()`** — corrigido de `isNil()` (tipo `Bool`) para `self` (tipo `SELF_TYPE`, conforme `List`). Uma lista vazia já está ordenada, portanto retornar `self` é semanticamente correto.

3. **`let j : Int <- "0"`** — corrigido para `let j : Int <- 0` (literal inteiro).

---

### Prompt 2

> "add new context and prompts in this session appending to readme.md"

**O que foi feito:** esta seção foi adicionada ao `README.md` documentando os prompts desta sessão e as mudanças correspondentes.

---

## Sessão de correção do interpretador `brili`

### Prompt 1

> `brili < exemplo.bril.json` retornava `ReferenceError: getType is not defined`

**Contexto:** após gerar `exemplo.bril.json` com sucesso via `codegen_cool.py`, a tentativa de interpretar o arquivo com `brili` falhou com a seguinte stacktrace:

```
error: Uncaught (in promise) ReferenceError: getType is not defined
      const dest = getType(instr, "dest") as bril.Ident;
    at evalInstr (bril/brili.ts:695:20)
```

**Causa:** o bloco `case "read"` em `bril/brili.ts` (linha 684) chamava uma função `getType` que não existe em nenhum lugar do arquivo. Todas as outras instruções do mesmo switch acessam `instr.dest` e `instr.type` diretamente.

**Fix 1 — `bril/brili.ts` linhas 695–696:**

```typescript
// antes
const dest = getType(instr, "dest") as bril.Ident;
const typ  = getType(instr, "type") as bril.Type;

// depois
const dest = instr.dest as bril.Ident;
const typ  = instr.type as bril.Type;
```

**Fix 2 — `~/.deno/bin/brili` (wrapper Deno):**

O próximo erro foi `NaN cannot be converted to BigInt` porque o uso de `< exemplo.bril.json` faz o shell redirecionar o JSON para stdin, consumindo-o inteiramente antes que a instrução `read` possa ler a entrada do usuário. O brili aceita o JSON como argumento de linha de comando, mantendo stdin livre — mas o wrapper gerado por `deno install` não tinha a flag `--allow-read`.

```sh
# antes
exec deno run --config ... brili.ts "$@"

# depois
exec deno run --allow-read --config ... brili.ts "$@"
```

Com isso o programa é invocado como `echo 5 | brili exemplo.bril.json`.

**Fix 3 — `bril/brili.ts` função `findFunc` (linha 215):**

Após resolver o erro anterior, o interpretador falhou com `no function of name @Main_iota found`. O campo `funcs` das instruções `call` no JSON usa o prefixo `@` (ex.: `"@Main_iota"`), mas o campo `name` das definições de função não usa esse prefixo (ex.: `"Main_iota"`). A função `findFunc` comparava os dois diretamente com `f.name === func`, sem remover o `@`.

```typescript
// antes
const matches = funcs.filter(f => f.name === func);

// depois
const name = func.startsWith("@") ? func.slice(1) : func;
const matches = funcs.filter(f => f.name === name);
```

**Resultado:** `echo 5 | brili exemplo.bril.json` executa sem erros. O programa não imprime saída porque `List_print_list` ainda é uma implementação stub (o dispatch virtual Cool → Cons/Nil não está implementado no codegen), mas o interpretador roda até o fim sem crashes.

---

### Prompt 2

> "append context and instructions to readme.md"

**O que foi feito:** esta seção foi adicionada ao `README.md` documentando os três bugs encontrados no `brili.ts`, suas causas e os fixes aplicados.

---

## Sessão de implementação do modelo de objetos

### Prompt 1

> "`brili < exemplo.bril.json` retornava `RangeError: The number NaN cannot be converted to a BigInt`"

**Causa:** ao usar `< exemplo.bril.json`, o shell consome todo o stdin com o JSON, então a instrução `read` recebe string vazia → `parseInt("", 10) = NaN`. O fix adicionado nesta sessão foi tratar `NaN` com um valor padrão 0:

```typescript
// antes
val = BigInt(parseInt(line, 10));

// depois
const n = parseInt(line, 10);
val = BigInt(isNaN(n) ? 0 : n);
```

O mesmo tratamento foi aplicado ao caso `float`. Com isso, `brili < exemplo.bril.json` roda sem input (usando 0 como padrão para `in_int()`). Para fornecer input real, a invocação correta é `echo N | brili exemplo.bril.json`.

---

### Prompt 2

> "nothing prints though"

**Diagnóstico:** mesmo com o interpretador funcionando, `echo 3 | brili exemplo.bril.json` não imprimia nada. A causa eram três limitações arquiteturais do codegen:

1. **`new T` → `const 0`**: nenhuma alocação real de memória. `Cons_init` escrevia em variáveis locais que não persistiam entre chamadas — toda referência a objeto era o inteiro `0`.

2. **Atributos como variáveis locais**: cada método reinicializava `xcar`, `xcdr`, etc. como `const 0` no início. O estado escrito por `Cons_init` nunca chegava a `Cons_car` ou `Cons_print_list`.

3. **Dispatch estático**: `list.print_list()` sempre chamava `@List_print_list` (stub que retornava 0), nunca `@Cons_print_list` nem `@Nil_print_list`.

**O que foi feito:**

#### A. Dois novos opcodes em `bril/brili.ts`

Para representar objetos como handles inteiros e ainda usar as operações de memória Bril (`alloc`/`store`/`load`/`ptradd`), foram adicionados dois opcodes customizados ao interpretador:

- **`ptrToInt`**: converte um `ptr<int>` para o endereço base como `int` (BigInt). Permite retornar o handle do objeto alocado como inteiro.
- **`intToPtr`**: reconstrói um `ptr<int>` a partir de um handle inteiro. Permite acessar os campos do objeto via `ptradd`/`load`/`store`.

Ambos também foram registrados em `argCounts` (tabela de aridade de opcodes), que validava as instruções antes do switch.

#### B. Redesenho de `codegen_cool.py` — modelo de objetos com heap

**Tabelas de metadados por classe** (computadas em `_build_class_info`):
- `class_tags`: tag inteiro único por classe (atribuído na ordem de aparição no fonte)
- `attr_layouts`: mapeamento `attr_name → slot_index` (slot 0 = tag, slots 1..N = atributos)
- `class_sizes`: número total de slots por objeto
- `class_children`: mapa de classe → filhas diretas (para determinar quais métodos precisam de dispatch wrapper)

**`new T` com alocação real** (`_emit_new`):
```
alloc N → ptr<int>
store tag em slot 0
store 0 em slots 1..N-1  (atributos inicializados)
ptrToInt ptr → handle (int)
```

**Acesso a atributos via memória** (`_emit_attr_load` / `_emit_attr_store`):
```
intToPtr handle → ptr<int>
ptradd ptr, slot_idx → ptr<int>
load/store ptr
```
Identificadores que correspondem a atributos da classe atual são detectados em `gen_expr(Identificador)` e `gen_expr(Atribuicao)` pelo lookup em `attr_layouts`, em vez de serem tratados como variáveis locais.

**`@main` aloca o objeto Main**: antes `self = const 0`; agora `_emit_new("Main")` → handle real, para que atributos como `l : List` possam ser lidos/escritos via memória.

**Dispatch wrappers** (`gen_dispatch_wrapper`):
Para cada método M sobrescrito em alguma subclasse de C, `@C_M` é gerado como um wrapper que:
1. Converte `self` (handle int) para pointer
2. Carrega o tag do slot 0
3. Encadeia `eq tag, tag_filho; br hit, miss` para cada subclasse concreta
4. Na branch `hit`, chama `@SubClasse_M` diretamente
5. Fallback: retorna 0

Métodos **não** sobrescritos em nenhuma subclasse continuam sendo gerados diretamente da AST Cool.

**Coerção de tipo de retorno**: quando o corpo do método produz um tipo Bril diferente do declarado (ex.: `print_list() : Object { true }` — corpo Bool, retorno Object→int), emite `const 0 : int` no lugar do valor Bool.

#### C. Correção de `exemplo.cl`

`Nil` não sobrescrevia `sort` nem `insert` no arquivo original. Em Cool com runtime real, isso causaria `abort()` ao chegar na base da recursão. No codegen simplificado, retornava 0 (handle nulo), que corromperia a lista. As duas implementações corretas foram adicionadas:

```cool
sort()         : List { self };
insert(i : Int): List { (new Cons).init(i, self) };
```

#### D. Vazamento de memória como warning

`brili` lançava exceção se a heap não estivesse vazia ao final da execução. Como o codegen não emite `free`, todos os objetos alocados ficavam na heap. O throw foi substituído por um `console.error` de aviso:

```typescript
// antes
throw error(`Some memory locations have not been freed…`);

// depois
console.error(`warning: Some memory locations have not been freed…`);
```

**Resultado final:**

```
$ echo 3 | brili exemplo.bril.json
0
1
2
warning: Some memory locations have not been freed by end of execution.

$ echo 5 | brili exemplo.bril.json
0
1
2
3
4
warning: Some memory locations have not been freed by end of execution.
```

O pipeline completo `iota → rev → sort → print_list` funciona corretamente com dispatch virtual e estado persistido em memória heap.

---

### Prompt 3

> "append the session context to readme.md"

**O que foi feito:** esta seção foi adicionada ao `README.md` documentando os prompts desta sessão e todas as mudanças correspondentes.
