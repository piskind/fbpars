import re
from dataclasses import dataclass, field
from datetime import datetime


LIBRARY_ID_RE = re.compile(r"Library ID:\s*(\d+)")
STARTED_RE = re.compile(r"Started running on\s+([A-Za-z]+\s+\d{1,2},\s*\d{4})")
DOMAIN_RE = re.compile(r"^([A-Z0-9][A-Z0-9.\-]+\.(COM|NET|ORG|SITE|SHOP|STORE|ONLINE|XYZ|IO|CO|ME|APP|TK|ML|GA|CF))$")

CTA_KEYWORDS = {
    "Learn More", "Shop Now", "Sign Up", "Send Message", "Subscribe",
    "Apply Now", "Get Offer", "Download", "Book Now", "Contact Us",
    "Get Quote", "Order Now", "Donate Now", "Watch More", "Get Showtimes",
    "See ad details", "Send messa...",
    "Más información", "Comprar ahora", "Suscribirse", "Enviar mensaje",
    "Solicitar ahora", "Reservar ahora", "Pedir ahora", "Descargar",
    "Obtener oferta", "Contáctenos", "Obtener cotización", "Donar ahora",
    "Ver más", "Ver horarios", "Registrarse",
    "Saiba mais", "Comprar agora", "Inscrever-se", "Enviar mensagem",
    "Candidatar-se", "Reservar agora", "Pedir agora", "Baixar",
    "Obter oferta", "Fale conosco", "Obter cotação", "Assinar",
    "Ver mais", "Doar agora",
    "En savoir plus", "Acheter", "Acheter maintenant", "S'inscrire", "S'abonner",
    "Envoyer un message", "Postuler maintenant", "Réserver maintenant",
    "Commander maintenant", "Télécharger", "Obtenir l'offre",
    "Nous contacter", "Demander un devis", "Faire un don", "Voir plus",
    "Mehr dazu", "Jetzt kaufen", "Registrieren",
}

CTA_KEYWORDS_LOWER = {kw.lower() for kw in CTA_KEYWORDS}

LEAD_FORM_MARKERS = {
    "Leave a message", "Submit your information", "Fill out the form",
    "Get a quote", "Request a quote",
    "Deja mensaje", "Dejar mensaje", "Solicitar información",
}

NOISE_LINES = {
    "Sorry, we're having trouble playing this video.",
    "Learn more",
    "Low impression count",
}


@dataclass
class ParsedCard:
    library_id: str | None = None
    is_active: bool = False
    started_at: datetime | None = None
    days_active: int = 0
    page_name: str | None = None
    title: str | None = None
    body_text: str | None = None
    caption: str | None = None
    cta_text: str | None = None
    link_url: str | None = None
    page_url: str | None = None
    display_url: str | None = None
    platforms: list[str] = field(default_factory=list)
    lead_form: bool = False
    used_in_ads_count: int = 1
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

    if any(marker in text for marker in LEAD_FORM_MARKERS):
        card.lead_form = True

    m = re.search(
        r'used in (\d+) ads|используется в (\d+) объявлениях',
        text, re.IGNORECASE,
    )
    if m:
        card.used_in_ads_count = int(m.group(1) or m.group(2))

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
        post = lines[sponsored_idx + 1:]
        body_lines = []
        post_domain = []
        domain_seen = False
        cta_seen = False

        for line in post:
            if line.startswith("Library ID:"):
                break
            if line.lower() in CTA_KEYWORDS_LOWER:
                card.cta_text = line
                cta_seen = True
                continue
            if cta_seen:
                continue
            if DOMAIN_RE.match(line):
                domain_seen = True
                continue
            if line in NOISE_LINES:
                continue
            if re.match(r"^\d+:\d+\s*/\s*\d+:\d+$", line):
                continue
            if not domain_seen:
                body_lines.append(line)
            else:
                post_domain.append(line)

        if body_lines:
            card.body_text = "\n".join(body_lines).strip() or None

        if post_domain:
            card.title = post_domain[0]
            if len(post_domain) > 1:
                card.caption = post_domain[1]

    return card
