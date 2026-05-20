import re
from dataclasses import dataclass, field
from datetime import datetime


LIBRARY_ID_RE = re.compile(r"Library ID:\s*(\d+)")
STARTED_RE = re.compile(r"Started running on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})")
DOMAIN_RE = re.compile(r"^([A-Z0-9][A-Z0-9.\-]+\.(COM|NET|ORG|SITE|SHOP|STORE|ONLINE|XYZ|IO|CO|ME|APP))$")


CTA_KEYWORDS = {
    "Learn More", "Shop Now", "Sign Up", "Send Message", "Subscribe",
    "Apply Now", "Get Offer", "Download", "Book Now", "Contact Us",
    "Get Quote", "Order Now", "Donate Now", "Watch More", "Get Showtimes",
    "See ad details", "Send messa...",
    # ES
    "Más información", "Comprar ahora", "Suscribirse", "Enviar mensaje",
    "Solicitar ahora", "Reservar ahora", "Pedir ahora", "Descargar",
    "Obtener oferta", "Contáctenos", "Obtener cotización", "Donar ahora",
    "Ver más", "Ver horarios", "Registrarse",
    # PT
    "Saiba mais", "Comprar agora", "Inscrever-se", "Enviar mensagem",
    "Candidatar-se", "Reservar agora", "Pedir agora", "Baixar",
    "Obter oferta", "Fale conosco", "Obter cotação", "Assinar",
    "Ver mais", "Doar agora",
    # FR
    "En savoir plus", "Acheter", "Acheter maintenant", "S'inscrire", "S'abonner",
    "Envoyer un message", "Postuler maintenant", "Réserver maintenant",
    "Commander maintenant", "Télécharger", "Obtenir l'offre",
    "Nous contacter", "Demander un devis", "Faire un don", "Voir plus",
    # DE
    "Mehr dazu", "Jetzt kaufen", "Registrieren",
}


@dataclass
class ParsedCard:
    library_id: str | None = None
    is_active: bool = False
    started_at: datetime | None = None
    days_active: int = 0
    page_name: str | None = None
    body_text: str | None = None
    cta_text: str | None = None
    link_url: str | None = None
    page_url: str | None = None
    display_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    poster_urls: list[str] = field(default_factory=list)
    raw_text: str | None = None


def parse_started_date(text: str) -> datetime | None:
    m = STARTED_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    for fmt in ("%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def extract_display_url(lines: list[str]) -> str | None:
    for line in lines:
        if DOMAIN_RE.match(line):
            return line
    return None


def parse_card_text(text: str) -> ParsedCard:
    card = ParsedCard(raw_text=text)

    m = LIBRARY_ID_RE.search(text)
    if m:
        card.library_id = m.group(1)

    head = text[:200]
    if "Inactive" in head:
        card.is_active = False
    elif "Active" in head:
        card.is_active = True

    card.started_at = parse_started_date(text)

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    card.display_url = extract_display_url(lines)

    sponsored_idx = None
    for i, line in enumerate(lines):
        if line == "Sponsored":
            sponsored_idx = i
            break

    if sponsored_idx is not None and sponsored_idx > 0:
        card.page_name = lines[sponsored_idx - 1]

    if sponsored_idx is not None and sponsored_idx + 1 < len(lines):
        body_lines = []
        for line in lines[sponsored_idx + 1:]:
            if line in CTA_KEYWORDS:
                card.cta_text = line
                break
            if line.startswith("Library ID:"):
                break
            if DOMAIN_RE.match(line):
                continue
            if line in {"Sorry, we're having trouble playing this video.", "Learn more"}:
                continue
            body_lines.append(line)
        if body_lines:
            card.body_text = "\n".join(body_lines).strip()

    return card