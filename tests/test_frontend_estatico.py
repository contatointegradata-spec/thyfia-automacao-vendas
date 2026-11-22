"""Guardas estáticas do frontend: pegam, sem abrir navegador, as três classes de erro
que já escaparam para a tela do cliente (ver docs/qa-frontend.md)."""
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ESTATICO = Path(__file__).resolve().parent.parent / "thyfia" / "static"
VOID = {"meta", "link", "br", "hr", "img", "input", "source", "use", "path", "circle", "rect", "stop"}


def _ler(nome: str) -> str:
    return (ESTATICO / nome).read_text(encoding="utf-8")


class _Arvore(HTMLParser):
    """Guarda, para cada elemento, a pilha de ancestrais (id/classes)."""

    def __init__(self):
        super().__init__()
        self.pilha: list[tuple[str, str, set]] = []
        self.itens: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = set((a.get("class") or "").split())
        self.itens.append({"tag": tag, "id": a.get("id"), "classes": classes,
                           "ancestrais": [(t, i, c) for t, i, c in self.pilha]})
        if tag not in VOID:
            self.pilha.append((tag, a.get("id"), classes))

    def handle_endtag(self, tag):
        for k in range(len(self.pilha) - 1, -1, -1):
            if self.pilha[k][0] == tag:
                del self.pilha[k:]
                break


class TestFrontendEstatico(unittest.TestCase):
    def test_seletor_de_classe_nao_e_definido_duas_vezes(self):
        """Duas regras `.pl-grade {…}` fizeram o grid do Piloto sobrescrever o dos cards de plano.
        Classes com o mesmo nome em telas diferentes se sobrescrevem; cada seletor simples vale uma vez
        (exceções abaixo são ajustes deliberados da mesma tela)."""
        css = re.sub(r"/\*.*?\*/", "", _ler("styles.css"), flags=re.S)
        vistos: dict[str, int] = {}
        for linha in css.splitlines():
            if linha.startswith((" ", "\t", "@", "}")) or "{" not in linha:
                continue
            seletor = linha.split("{")[0].strip()
            if re.fullmatch(r"\.[a-z0-9_-]+", seletor):
                vistos[seletor] = vistos.get(seletor, 0) + 1
        permitidos = {".aj-busca", ".aj-sec", ".ig-preview-img", ".pl-lista", ".sidebar"}  # reforços da mesma tela
        repetidos = sorted(k for k, n in vistos.items() if n > 1 and k not in permitidos)
        self.assertEqual(repetidos, [], f"classes definidas mais de uma vez (renomeie a da outra tela): {repetidos}")

    def test_paineis_ficam_dentro_de_uma_view(self):
        """O painel 'O que o Cérebro aprendeu' ficou fora de #view-cerebro e apareceu em todas as telas."""
        arv = _Arvore()
        arv.feed(_ler("index.html"))
        soltos = []
        for it in arv.itens:
            if "painel" not in it["classes"]:
                continue
            dentro_do_app = any(c and "conteudo" in c for _, _, c in it["ancestrais"])
            em_view = any(c and "view" in c for _, _, c in it["ancestrais"])
            if dentro_do_app and not em_view:
                soltos.append(it["id"] or sorted(it["classes"]))
        self.assertEqual(soltos, [], f"painéis soltos fora de uma .view: {soltos}")

    def test_data_ativo_so_existe_no_admin(self):
        """`data-ativo` nas abas do login e no botão Pausar/Ligar do Piloto disparava, a cada clique,
        o manipulador global do admin (chamada à API de admin + aviso de erro)."""
        for nome in ("index.html", "app.js", "piloto.js", "styles.css"):
            self.assertNotRegex(_ler(nome), r"data-ativo|dataset\.ativo", f"{nome} usa data-ativo (reservado ao admin)")
        planos = _ler("planos.js")
        self.assertIn('closest("#view-admin', planos, "o clique global do admin precisa ficar restrito a #view-admin")
        self.assertNotRegex(planos, r'closest\("\[data-conceder\]', "seletor global sem escopo")

    def test_interface_nao_usa_emoji_como_icone(self):
        """Design system ThyfIA: emoji só dentro de texto de mensagem/legenda enviado ao público; na interface
        os ícones vêm do sprite (`ic("nome")`). Linhas com a legenda-modelo do Instagram são a exceção."""
        emoji = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2B06\u23F0]")
        ok_legenda = ("Achados com preço bom", "Link na bio")
        achados = []
        for nome in ("index.html", "app.js", "planos.js", "piloto.js", "../conteudo/ajuda.html"):
            for n, linha in enumerate(_ler(nome).splitlines(), 1):
                if emoji.search(linha) and not any(o in linha for o in ok_legenda):
                    achados.append(f"{nome}:{n}")
        self.assertEqual(achados, [], f"emoji na interface (use um ícone do sprite): {achados}")

    def test_icones_usados_existem_no_sprite(self):
        """`ic("x")` / `#i-x` apontando para um ícone que não existe renderiza um quadrado vazio."""
        html = _ler("index.html")
        sprite = set(re.findall(r'<symbol id="i-([a-z-]+)"', html))
        usados = set(re.findall(r'#i-([a-z-]+)', html))
        for nome in ("app.js", "planos.js", "piloto.js"):
            usados |= set(re.findall(r'\bic\("([a-z-]+)"\)', _ler(nome)))
        usados |= {m for m in re.findall(r'icone: "([a-z-]{3,})"', _ler("app.js"))}
        self.assertEqual(sorted(usados - sprite), [], "ícones sem <symbol> no sprite")

    def test_tokens_e_fontes_do_design_system_existem(self):
        """tokens.css referencia as fontes locais (nada de Google Fonts) e o front usa só tokens, sem degradê roxo-ciano."""
        tok = _ler("tokens.css")
        for arq in re.findall(r'url\("/static/([^"]+)"\)', tok):
            self.assertTrue((ESTATICO / arq).exists(), f"fonte ausente: {arq}")
        self.assertIn("--brand: #7B4DEF", tok)
        self.assertNotIn("fonts.googleapis", _ler("index.html"))
        css = _ler("styles.css")
        self.assertNotRegex(css, r"linear-gradient\([^)]*(#22d3ee|#8b5cf6|var\(--accent\)|var\(--brand\))", "degradê roxo-ciano não faz parte da identidade")


if __name__ == "__main__":
    unittest.main()
