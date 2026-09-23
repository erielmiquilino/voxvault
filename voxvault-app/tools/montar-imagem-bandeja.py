"""Put the four tray icons side by side, labelled, for the README.

Reads the very PNGs the app embeds (src-tauri/icons/bandeja/), so the picture
can never drift from what the tray shows. Run with Pillow available:

    uv run --no-project --with pillow python voxvault-app/tools/montar-imagem-bandeja.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

RAIZ = Path(__file__).resolve().parents[2]
ICONES = RAIZ / "voxvault-app" / "src-tauri" / "icons" / "bandeja"
SAIDA = RAIZ / "docs" / "imagens" / "bandeja.png"

ESTADOS = [("ocioso", "ocioso"), ("gravando", "gravando"), ("pausado", "pausado"),
           ("falha", "serviço indisponível")]
LADO = 96          # each icon, scaled up without smoothing: they are pixel-exact
CELULA = 200
ALTURA = 170
FUNDO = (243, 244, 246)
TEXTO = (31, 41, 55)


def _fonte(tamanho: int):
    for nome in ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(nome, tamanho)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> int:
    imagem = Image.new("RGB", (CELULA * len(ESTADOS), ALTURA), FUNDO)
    desenho = ImageDraw.Draw(imagem)
    fonte = _fonte(18)
    for i, (arquivo, rotulo) in enumerate(ESTADOS):
        icone = Image.open(ICONES / f"{arquivo}.png").convert("RGBA")
        icone = icone.resize((LADO, LADO), Image.NEAREST)
        x = i * CELULA + (CELULA - LADO) // 2
        imagem.paste(icone, (x, 22), icone)
        largura = desenho.textlength(rotulo, font=fonte)
        desenho.text((i * CELULA + (CELULA - largura) / 2, 22 + LADO + 16), rotulo,
                     fill=TEXTO, font=fonte)
    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    imagem.save(SAIDA, optimize=True)
    print(f"{SAIDA} ({imagem.width}x{imagem.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
