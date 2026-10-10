"""
routine_interpreter.py
========================

Interpretador do arquivo de rotina do experimento (a linguagem que
descreve as fases/condições da sessão). Reúne tudo que diz respeito a
"ler e aplicar o arquivo de rotina":

    1) Lexer    -> transforma o texto em tokens
    2) Parser   -> transforma os tokens em uma árvore de dados
                   {"config": {...}, "routines": [...]}
    3) RoutineManager -> usa essa árvore durante o experimento, checando
                   condições (total de reforços / tempo / tempo parado)
                   e aplicando as ações correspondentes (mudar esquema,
                   raio, resposta alvo, regredir um passo etc.) nas
                   pepitas.

`model.py` continua sendo o dono das classes de domínio (esquemas de
reforço, controlador do sujeito, câmera); este módulo só as importa
para poder instanciá-las a partir do texto da rotina.

------------------------------------------------------------------------
SINTAXE DO ARQUIVO DE ROTINA (baseada em indentação, tipo Python)
------------------------------------------------------------------------
programa      := config_bloco? rotina*

config_bloco  := "config" ":" NEWLINE INDENT atribuicao+ DEDENT

rotina        := "when" condicao ":" NEWLINE INDENT acao+ DEDENT

condicao      := IDENT dicionario? COMPARADOR NUMERO
COMPARADOR    := ">=" | "<=" | "==" | ">" | "<"

acao          := "end" NEWLINE
               | atribuicao
               | pepita_bloco

pepita_bloco  := "pepita" NUMERO ":" NEWLINE INDENT atribuicao+ DEDENT

atribuicao    := IDENT "=" valor NEWLINE

valor         := NUMERO | IDENT | lista | dicionario
lista         := "[" IDENT ("," IDENT)* "]"
dicionario    := "{" (par ("," par)*)? "}"
par           := IDENT ":" valor

O dicionário é um valor genérico (pode aparecer em qualquer atribuição,
não só em condições) — é a forma da DSL de representar parâmetros
nomeados, tipo `{mode: any, threshold: 240}`.

------------------------------------------------------------------------
Exemplo:

    config:
        n_pepitas = 2
        cod_duration = 5.0

    when total_rewards >= 0:
        cod_duration = 0.0
        target_response = [scroll_click, scroll, right_click]
        pepita 0:
            radius = 5000
            schedule = FR-1
        pepita 1:
            radius = 5000
            schedule = FR-1

    # Se qualquer uma das pepitas ficar mais de 4 minutos (240s) sem
    # reforço, regride um passo no shaping (facilita a contingência de
    # novo, reaplicando as ações do passo anterior).
    when idle_time {mode: any} > 240:
        step_back = 1

    when time >= 2700:
        end

Regras de indentação:
    - Use espaços (tabs são expandidos para 4 espaços cada).
    - Todas as linhas de um mesmo bloco devem ter a mesma indentação.
    - Linhas em branco são ignoradas e não afetam a indentação.
    - Comentários com "#" ou "//" até o fim da linha.

------------------------------------------------------------------------
CONDIÇÕES SUPORTADAS
------------------------------------------------------------------------
- total_rewards   : soma de reforços de todas as pepitas (dispara uma
                     única vez, quando o operador é satisfeito).
- time            : tempo decorrido de sessão, em segundos (dispara uma
                     única vez).
- idle_time       : tempo (em segundos) desde o último reforço de uma
                     pepita. Ao contrário das demais, é reavaliada
                     continuamente (não "completa" de forma definitiva).
                     Aceita o parâmetro opcional `mode` no dicionário:
                       - "any" (padrão): dispara se QUALQUER pepita
                         estiver parada há mais tempo que o limite.
                       - "all": dispara só se TODAS estiverem.

------------------------------------------------------------------------
AÇÕES SUPORTADAS (além das já existentes: cod_duration, target_response,
min_scroll_y, scale_world, blocos "pepita N")
------------------------------------------------------------------------
- step_back = N   : regride N passos no shaping, reaplicando as ações
                     de uma rotina de "total_rewards" anterior já
                     alcançada (facilita a contingência de novo).
"""

import re
import operator as _op
import pygame

from model import (
    FixedRatio,
    VariableInterval,
    Extinction,
    RandomInterval,
    DifferentialReinforcementOfLowRates,
)


# ==========================================================================
# 1) LEXER
# ==========================================================================

class Token:
    __slots__ = ("type", "value", "line")

    def __init__(self, type_, value, line):
        self.type = type_
        self.value = value
        self.line = line

    def __repr__(self):
        return f"Token({self.type}, {self.value!r}, line={self.line})"


# Tokens reconhecidos DENTRO de uma linha (indentação é tratada à parte).
# Ordem importa: padrões mais específicos primeiro.
TOKEN_SPEC = [
    ("NUMBER",   r"\d+\.\d+|\d+"),
    ("GE",       r">="),
    ("LE",       r"<="),
    ("EQEQ",     r"=="),
    ("GT",       r">"),
    ("LT",       r"<"),
    ("EQUALS",   r"="),
    ("COLON",    r":"),
    ("LBRACE",   r"\{"),
    ("RBRACE",   r"\}"),
    ("LBRACKET", r"\["),
    ("RBRACKET", r"\]"),
    ("COMMA",    r","),
    ("SKIP",     r"[ \t]+"),
    # Identificadores: permite hífen e dígitos para tokens tipo FR-1, VI-24,
    # RI-10s, DRL-5s, EXT, scroll_click, right_click, total_rewards etc.
    ("IDENT",    r"[A-Za-z_][A-Za-z0-9_\-]*"),
    ("MISMATCH", r"."),
]

MASTER_PATTERN = re.compile(
    "|".join(f"(?P<{name}>{pattern})" for name, pattern in TOKEN_SPEC)
)

KEYWORDS = {"config", "when", "end", "pepita"}

COMMENT_RE = re.compile(r"(#.*$)|(//.*$)")


class LexerError(Exception):
    pass


class Lexer:
    """
    Transforma o texto bruto em uma lista de Tokens, gerando também
    tokens estruturais INDENT / DEDENT / NEWLINE a partir da indentação
    de cada linha (mesmo esquema usado pelo tokenizer do Python).
    Chaves "{ }" são tokens comuns de uma linha (usadas para dicionários
    inline) e não interferem na indentação — só ":" no fim de linha abre
    um bloco indentado.
    """

    def __init__(self, source):
        self.source = source

    def tokenize(self):
        tokens = []
        indent_stack = [0]
        lines = self.source.splitlines()
        last_line_no = 1

        for line_no, raw_line in enumerate(lines, start=1):
            last_line_no = line_no

            # remove comentários antes de qualquer outra coisa
            line = COMMENT_RE.sub("", raw_line)

            if line.strip() == "":
                continue  # linha em branco: ignorada, não afeta indentação

            expanded = line.expandtabs(4)
            indent_len = len(expanded) - len(expanded.lstrip(" "))
            content = expanded[indent_len:]

            if indent_len > indent_stack[-1]:
                indent_stack.append(indent_len)
                tokens.append(Token("INDENT", indent_len, line_no))
            else:
                while indent_len < indent_stack[-1]:
                    indent_stack.pop()
                    tokens.append(Token("DEDENT", indent_len, line_no))
                if indent_len != indent_stack[-1]:
                    raise LexerError(
                        f"Indentação inconsistente na linha {line_no}"
                    )

            tokens.extend(self._tokenize_line(content, line_no))
            tokens.append(Token("NEWLINE", None, line_no))

        # fecha todos os blocos ainda abertos no fim do arquivo
        while len(indent_stack) > 1:
            indent_stack.pop()
            tokens.append(Token("DEDENT", 0, last_line_no))

        tokens.append(Token("EOF", None, last_line_no))
        return tokens

    def _tokenize_line(self, content, line_no):
        pos = 0
        toks = []
        while pos < len(content):
            match = MASTER_PATTERN.match(content, pos)
            if not match:
                raise LexerError(
                    f"Caractere inesperado {content[pos]!r} na linha {line_no}"
                )
            kind = match.lastgroup
            value = match.group()
            pos = match.end()

            if kind == "SKIP":
                continue
            if kind == "MISMATCH":
                raise LexerError(
                    f"Caractere inesperado {value!r} na linha {line_no}"
                )

            if kind == "NUMBER":
                value = float(value) if "." in value else int(value)
            elif kind == "IDENT" and value in KEYWORDS:
                kind = value.upper()  # vira CONFIG, WHEN, END ou PEPITA

            toks.append(Token(kind, value, line_no))
        return toks


# ==========================================================================
# 2) PARSER (recursive descent)
# ==========================================================================

class ParserError(Exception):
    pass


class Parser:
    """Consome os Tokens e produz a estrutura {"config": ..., "routines": [...]}"""

    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    # -- utilidades ---------------------------------------------------------
    def _peek(self):
        return self.tokens[self.pos]

    def _advance(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def _check(self, type_):
        return self._peek().type == type_

    def _expect(self, type_):
        tok = self._peek()
        if tok.type != type_:
            raise ParserError(
                f"Esperado token {type_}, obtido {tok.type} ({tok.value!r}) "
                f"na linha {tok.line}"
            )
        return self._advance()

    # -- regras da gramática --------------------------------------------------
    def parse(self):
        config = {}
        routines = []

        if self._check("CONFIG"):
            config = self._parse_config()

        while not self._check("EOF"):
            routines.append(self._parse_when())

        return {"config": config, "routines": routines}

    def _parse_block(self, parse_line_fn):
        """Consome ':' NEWLINE INDENT (linhas)+ DEDENT genericamente."""
        self._expect("COLON")
        self._expect("NEWLINE")
        self._expect("INDENT")
        while not self._check("DEDENT"):
            parse_line_fn()
        self._expect("DEDENT")

    def _parse_config(self):
        self._expect("CONFIG")
        cfg = {}

        def line():
            key, value = self._parse_assignment()
            cfg[key] = value

        self._parse_block(line)
        return cfg

    def _parse_when(self):
        self._expect("WHEN")
        condition = self._parse_condition()

        actions = {}
        state = {"end": False}

        def line():
            if self._check("END"):
                self._advance()
                self._expect("NEWLINE")
                state["end"] = True
                return
            if self._check("PEPITA"):
                pid, pconfig = self._parse_pepita()
                actions[str(pid)] = pconfig
                return
            key, value = self._parse_assignment()
            actions[key] = value

        self._parse_block(line)

        routine = {"condition": condition, "actions": actions}
        if state["end"]:
            routine["end"] = True
        return routine

    def _parse_condition(self):
        ident_tok = self._expect("IDENT")

        params = {}
        if self._check("LBRACE"):
            params = self._parse_dict()

        comparator = self._parse_comparator()
        number_tok = self._expect("NUMBER")

        return {
            "type": ident_tok.value,
            "value": number_tok.value,
            "op": comparator,
            "params": params,
        }

    def _parse_comparator(self):
        tok = self._peek()
        mapping = {"GE": ">=", "LE": "<=", "EQEQ": "==", "GT": ">", "LT": "<"}
        if tok.type in mapping:
            self._advance()
            return mapping[tok.type]
        raise ParserError(
            f"Esperado operador de comparação, obtido {tok.type} na linha {tok.line}"
        )

    def _parse_pepita(self):
        self._expect("PEPITA")
        num_tok = self._expect("NUMBER")
        cfg = {}

        def line():
            key, value = self._parse_assignment()
            cfg[key] = value

        self._parse_block(line)
        return int(num_tok.value), cfg

    def _parse_assignment(self):
        ident_tok = self._expect("IDENT")
        self._expect("EQUALS")
        value = self._parse_value()
        self._expect("NEWLINE")
        return ident_tok.value, value

    def _parse_value(self):
        tok = self._peek()
        if tok.type == "NUMBER":
            self._advance()
            return tok.value
        if tok.type == "LBRACKET":
            return self._parse_list()
        if tok.type == "LBRACE":
            return self._parse_dict()
        if tok.type == "IDENT":
            self._advance()
            return tok.value
        raise ParserError(
            f"Valor inválido: {tok.type} ({tok.value!r}) na linha {tok.line}"
        )

    def _parse_list(self):
        self._expect("LBRACKET")
        items = []
        if not self._check("RBRACKET"):
            items.append(self._expect("IDENT").value)
            while self._check("COMMA"):
                self._advance()
                items.append(self._expect("IDENT").value)
        self._expect("RBRACKET")
        return items

    def _parse_dict(self):
        """dicionario := '{' (par (',' par)*)? '}'   par := IDENT ':' valor"""
        self._expect("LBRACE")
        result = {}
        if not self._check("RBRACE"):
            key, value = self._parse_pair()
            result[key] = value
            while self._check("COMMA"):
                self._advance()
                key, value = self._parse_pair()
                result[key] = value
        self._expect("RBRACE")
        return result

    def _parse_pair(self):
        key_tok = self._expect("IDENT")
        self._expect("COLON")
        value = self._parse_value()
        return key_tok.value, value


# ==========================================================================
# 3) API DE ALTO NÍVEL DO PARSER
# ==========================================================================

def parse_routine_file(path):
    """Lê um arquivo .txt na DSL e devolve o dict {"config":..., "routines":...}"""
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()

    tokens = Lexer(source).tokenize()
    return Parser(tokens).parse()


def parse_routine_string(source):
    """Mesma coisa que parse_routine_file, mas a partir de uma string em memória."""
    tokens = Lexer(source).tokenize()
    return Parser(tokens).parse()


# ==========================================================================
# 4) GERENCIADOR DE ROTINAS
# ==========================================================================

# Comparadores suportados nas condições (mapeados a partir do "op" que o
# parser já guarda). Antes o RoutineManager ignorava esse campo e sempre
# comparava com ">="; agora ele respeita o operador escrito no arquivo.
_COMPARATORS = {
    ">=": _op.ge,
    "<=": _op.le,
    "==": _op.eq,
    ">":  _op.gt,
    "<":  _op.lt,
}


class RoutineManager:
    """Gerencia as fases do experimento lendo condições de um arquivo de rotina."""

    def __init__(self, routine_path, pepitas, camera):
        self.pepitas            = pepitas
        self.routines           = []
        self.completed_routines = set()
        self.start_time         = pygame.time.get_ticks()
        self.cod_duration       = 0
        
        self.shaping_progression = []
        self.shaping_pointer     = -1
        
        self.shaping_active      = False
        self.shaping_penalty     = 0
        
        self.load_routines(routine_path)
        self.camera = camera

    def load_routines(self, routine_path):
        try:
            data = parse_routine_file(routine_path)
            self.routines     = data.get("routines", [])
            config            = data.get("config", {})
            self.cod_duration = config.get("cod_duration", 0.0) * 1000

        except FileNotFoundError:
            print(f"Aviso: Arquivo de rotina não encontrado: {routine_path}")
        except (LexerError, ParserError) as e:
            print(f"Erro: O arquivo {routine_path} possui uma rotina inválida. {e}")

    def _parse_schedule(self, schedule_str):
        if schedule_str == "EXT": return Extinction()
        fr_match = re.match(r"FR-(\d+)", schedule_str)
        if fr_match: return FixedRatio(int(fr_match.group(1)))
        vi_match = re.match(r"VI-(\d+)s?", schedule_str)
        if vi_match: return VariableInterval(int(vi_match.group(1)))
        ri_match = re.match(r"RI-(\d+)s?", schedule_str)
        if ri_match: return RandomInterval(int(ri_match.group(1)))
        drl_match = re.match(r"DRL-(\d+)s?", schedule_str)
        if drl_match: return DifferentialReinforcementOfLowRates(int(drl_match.group(1)))
        print(f"Aviso: Esquema '{schedule_str}' desconhecido. Usando FR-1.")
        return FixedRatio(1)

    def _check_idle_time(self, condition, now_ms):
        cmp_fn        = _COMPARATORS.get(condition.get("op", ">"), _op.gt)
        threshold_ms  = condition.get("value", 0) * 1000
        mode          = condition.get("params", {}).get("mode", "any")

        idle_flags = []
        for p in self.pepitas:
            idle_ms = now_ms - p.controller.last_reward_time
            idle_flags.append(cmp_fn(idle_ms, threshold_ms))

        if not idle_flags:
            return False

        return any(idle_flags) if mode == "any" else all(idle_flags)

    def check_routine(self):
        """Verifica se alguma condição do arquivo foi atingida para mudar de fase."""
        reforcos_totais_reais = sum(p.controller.total_rewards for p in self.pepitas)
        
        reforcos_efetivos = reforcos_totais_reais - self.shaping_penalty
        
        now_ms           = pygame.time.get_ticks()
        tempo_decorrido_ms = now_ms - self.start_time

        for index, routine in enumerate(self.routines):
            condition  = routine.get("condition", {})
            cond_type  = condition.get("type")
            cond_value = condition.get("value")
            cmp_fn     = _COMPARATORS.get(condition.get("op", ">="), _op.ge)

            if cond_type == "idle_time":
                if self._check_idle_time(condition, now_ms):
                    self._apply_actions(routine.get("actions", {}))
                    for p in self.pepitas:
                        p.controller.last_reward_time = now_ms
                continue

            if index in self.completed_routines:
                continue

            condicao_atingida = False

            if cond_type == "total_rewards":
                condicao_atingida = cmp_fn(reforcos_efetivos, cond_value)
            elif cond_type == "time":
                condicao_atingida = cmp_fn(tempo_decorrido_ms, cond_value * 1000)

            if condicao_atingida:
                self._apply_actions(routine.get("actions", {}))
                self.completed_routines.add(index)

                if cond_type == "total_rewards":
                    self.shaping_progression.append(index)
                    self.shaping_pointer = len(self.shaping_progression) - 1

                if routine.get("end", False):
                    return True

        return False

    def _step_back(self, steps=1):
        """Regride passos no shaping e apaga a conclusão deles."""
        if not self.shaping_active or not self.shaping_progression:
            return

        current_pointer = self.shaping_pointer
        new_pointer = max(0, current_pointer - int(steps))

        if new_pointer == current_pointer:
            return

        current_index = self.shaping_progression[current_pointer]
        target_index  = self.shaping_progression[new_pointer]

        # 1. Calcula a diferença de pontos entre a fase atual e a fase-alvo de recuo
        current_req = self.routines[current_index]["condition"]["value"]
        target_req  = self.routines[target_index]["condition"]["value"]
        
        # 2. Aplica essa diferença como penalidade. 
        self.shaping_penalty += (current_req - target_req)

        # 3. Remove as fases desfeitas dos registros de conclusão
        for p in range(new_pointer + 1, len(self.shaping_progression)):
            idx_to_remove = self.shaping_progression[p]
            if idx_to_remove in self.completed_routines:
                self.completed_routines.remove(idx_to_remove)

        self.shaping_progression = self.shaping_progression[:new_pointer + 1]
        self.shaping_pointer = new_pointer
        
        # 4. Reaplica as ações do ambiente facilitado
        self._apply_actions(self.routines[target_index].get("actions", {}))

    def _apply_actions(self, actions):
        # Permite desligar o rollback inserindo "shaping_active = 0" na rotina
        if "shaping_active" in actions:
            self.shaping_active = bool(actions["shaping_active"])
            
        if "cod_duration" in actions:
            self.cod_duration = actions["cod_duration"] * 1000

        if "target_response" in actions:
            resps = actions["target_response"]
            if isinstance(resps, str): resps = [resps]
            for p in self.pepitas:
                p.controller.set_target_responses(resps)

        if "min_scroll_y" in actions:
            for p in self.pepitas:
                p.controller.set_min_scroll_y(actions["min_scroll_y"])

        if "scale_world" in actions:
            self.camera.scale_world(actions['scale_world'])

        if "step_back" in actions:
            self._step_back(actions["step_back"])

        for key, config in actions.items():
            if key.isdigit():
                pepita_id = int(key)

                if pepita_id < len(self.pepitas):
                    pepita = self.pepitas[pepita_id]

                    if "radius" in config:
                        pepita.controller.set_radius(config["radius"])

                    if "schedule" in config:
                        new_schedule = self._parse_schedule(config["schedule"])
                        pepita.controller.change_schedule(new_schedule)

                    if "target_response" in config:
                        resps = config["target_response"]
                        if isinstance(resps, str): resps = [resps]
                        pepita.controller.set_target_responses(resps)

                    if "min_scroll_y" in config:
                        pepita.controller.set_min_scroll_y(config["min_scroll_y"])