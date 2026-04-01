# Este arquivo serve para testar string com caractere nulo real,
# que é difícil de inserir manualmente em um .cl comum.

codigo = 'class Main { s : String <- "ab\x00cd"; };'

with open("i8_string_com_caractere_nulo_real.cl", "w", encoding="utf-8", newline="") as f:
    f.write(codigo)
