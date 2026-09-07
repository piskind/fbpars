"""Родные слова по языкам — то, чего в словаре нет вовсе.

Наш словарь на 40 000 слов намыт из англоязычной выдачи США: ни иероглифов, ни арабицы.
Из-за этого корпуса zh/ar/th/ja/ko/he/hi/fa мы можем трогать только языковым фильтром,
а перебирать словом — нет. Замер 22.08 показал, чего это стоит: арабское `مجانا`
(«бесплатно») одним словом дало 54 новых крео из 100 карточек — столько же, сколько
весь языковой срез целиком.

Списки короткие и рекламные: берём понятия, которые реально пишут в объявлениях —
бесплатно, скидка, купить, похудение, кредит, казино, бонус, заработок. Это семена;
дальше список по каждому гео дорастает сам из журнала срезов (результативные слова).

Категории соответствуют структуре, которую просил заказчик:
  reklama   — общерекламные (бесплатно, скидка, акция, купить, заказать)
  nutra     — похудение, суставы, потенция, зрение, паразиты
  bolezni   — диабет, давление, простатит, варикоз, грибок
  gambling  — казино, слоты, ставки, бонус, джекпот
  finansy   — кредит, займ, инвестиции, заработок, крипта
"""

NATIVE = {
    "zh": {
        "reklama": ["免费", "优惠", "折扣", "促销", "限时", "购买", "订购", "特价"],
        "nutra": ["减肥", "瘦身", "关节", "护发", "美白", "视力"],
        "bolezni": ["糖尿病", "血压", "前列腺", "静脉曲张", "灰指甲", "痔疮"],
        "gambling": ["赌场", "老虎机", "投注", "奖金", "彩票"],
        "finansy": ["贷款", "投资", "赚钱", "理财", "比特币"],
    },
    "ar": {
        "reklama": ["مجانا", "تخفيض", "عرض", "خصم", "اشتري", "توصيل", "الآن"],
        "nutra": ["تخسيس", "انقاص الوزن", "المفاصل", "الشعر", "البشرة"],
        "bolezni": ["السكري", "ضغط الدم", "البروستاتا", "الدوالي", "البواسير"],
        "gambling": ["كازينو", "رهان", "مكافأة", "يانصيب"],
        "finansy": ["قرض", "استثمار", "ربح", "تمويل", "عملة رقمية"],
    },
    "th": {
        "reklama": ["ฟรี", "ส่วนลด", "โปรโมชั่น", "สั่งซื้อ", "ราคาพิเศษ"],
        "nutra": ["ลดน้ำหนัก", "ลดความอ้วน", "ข้อเข่า", "ผมร่วง", "ผิวขาว"],
        "bolezni": ["เบาหวาน", "ความดัน", "ต่อมลูกหมาก", "เส้นเลือดขอด", "ริดสีดวง"],
        "gambling": ["คาสิโน", "สล็อต", "แทงบอล", "โบนัส", "หวย"],
        "finansy": ["สินเชื่อ", "เงินกู้", "ลงทุน", "หาเงิน", "คริปโต"],
    },
    "ja": {
        "reklama": ["無料", "割引", "セール", "限定", "購入", "お得"],
        "nutra": ["ダイエット", "痩せる", "関節", "育毛", "美白"],
        "bolezni": ["糖尿病", "血圧", "前立腺", "静脈瘤", "水虫"],
        "gambling": ["カジノ", "スロット", "ベット", "ボーナス"],
        "finansy": ["ローン", "投資", "副業", "稼ぐ", "仮想通貨"],
    },
    "ko": {
        "reklama": ["무료", "할인", "특가", "구매", "이벤트"],
        "nutra": ["다이어트", "체중감량", "관절", "탈모", "미백"],
        "bolezni": ["당뇨", "혈압", "전립선", "하지정맥류", "무좀"],
        "gambling": ["카지노", "슬롯", "배팅", "보너스"],
        "finansy": ["대출", "투자", "부업", "재테크", "코인"],
    },
    "he": {
        "reklama": ["חינם", "הנחה", "מבצע", "לקנות", "משלוח"],
        "nutra": ["הרזיה", "ירידה במשקל", "מפרקים", "שיער", "עור"],
        "bolezni": ["סוכרת", "לחץ דם", "ערמונית", "טחורים"],
        "gambling": ["קזינו", "הימורים", "בונוס"],
        "finansy": ["הלוואה", "השקעה", "להרוויח", "קריפטו"],
    },
    "hi": {
        "reklama": ["मुफ्त", "छूट", "ऑफर", "खरीदें", "सस्ता"],
        "nutra": ["वजन घटाना", "मोटापा", "जोड़ों", "बाल", "त्वचा"],
        "bolezni": ["मधुमेह", "रक्तचाप", "प्रोस्टेट", "बवासीर"],
        "gambling": ["कैसीनो", "सट्टा", "बोनस", "लॉटरी"],
        "finansy": ["लोन", "निवेश", "कमाई", "क्रिप्टो"],
    },
    "fa": {
        "reklama": ["رایگان", "تخفیف", "پیشنهاد", "خرید", "ارسال"],
        "nutra": ["لاغری", "کاهش وزن", "مفاصل", "ریزش مو", "پوست"],
        "bolezni": ["دیابت", "فشار خون", "پروستات", "واریس", "بواسیر"],
        "gambling": ["کازینو", "شرط بندی", "بونوس"],
        "finansy": ["وام", "سرمایه گذاری", "درآمد", "ارز دیجیتال"],
    },
    "vi": {
        "reklama": ["miễn phí", "giảm giá", "khuyến mãi", "mua ngay", "ưu đãi"],
        "nutra": ["giảm cân", "xương khớp", "rụng tóc", "trắng da", "sinh lý"],
        "bolezni": ["tiểu đường", "huyết áp", "tuyến tiền liệt", "trĩ", "giãn tĩnh mạch"],
        "gambling": ["casino", "nổ hũ", "cá cược", "khuyến mãi nạp"],
        "finansy": ["vay tiền", "đầu tư", "kiếm tiền", "tiền ảo"],
    },
    "id": {
        "reklama": ["gratis", "diskon", "promo", "beli sekarang", "termurah"],
        "nutra": ["pelangsing", "turun berat badan", "sendi", "rambut rontok", "kulit"],
        "bolezni": ["diabetes", "darah tinggi", "prostat", "wasir", "asam urat"],
        "gambling": ["kasino", "slot", "taruhan", "bonus"],
        "finansy": ["pinjaman", "investasi", "penghasilan", "kripto"],
    },
    "tr": {
        "reklama": ["ücretsiz", "indirim", "kampanya", "satın al", "kargo bedava"],
        "nutra": ["zayıflama", "kilo verme", "eklem", "saç dökülmesi", "cilt"],
        "bolezni": ["şeker hastalığı", "tansiyon", "prostat", "varis", "hemoroid"],
        "gambling": ["casino", "slot", "bahis", "bonus"],
        "finansy": ["kredi", "yatırım", "para kazan", "kripto"],
    },
    "ru": {
        "reklama": ["бесплатно", "скидка", "акция", "заказать", "доставка", "распродажа"],
        "nutra": ["похудение", "сустав", "потенция", "выпадение волос", "зрение", "паразиты"],
        "bolezni": ["диабет", "давление", "простатит", "варикоз", "грибок", "геморрой"],
        "gambling": ["казино", "слоты", "ставки", "бонус", "фриспины"],
        "finansy": ["кредит", "займ", "инвестиции", "заработок", "криптовалюта"],
    },
    "uk": {
        "reklama": ["безкоштовно", "знижка", "акція", "замовити", "доставка"],
        "nutra": ["схуднення", "суглоби", "потенція", "випадіння волосся"],
        "bolezni": ["діабет", "тиск", "простатит", "варикоз", "грибок"],
        "gambling": ["казино", "слоти", "ставки", "бонус"],
        "finansy": ["кредит", "позика", "інвестиції", "заробіток"],
    },
    "el": {
        "reklama": ["δωρεάν", "έκπτωση", "προσφορά", "αγορά"],
        "nutra": ["αδυνάτισμα", "αρθρώσεις", "τριχόπτωση", "δέρμα"],
        "bolezni": ["διαβήτης", "πίεση", "προστάτης", "αιμορροΐδες"],
        "gambling": ["καζίνο", "στοίχημα", "μπόνους"],
        "finansy": ["δάνειο", "επένδυση", "κέρδος"],
    },
    "pl": {
        "reklama": ["za darmo", "zniżka", "promocja", "kup teraz", "dostawa"],
        "nutra": ["odchudzanie", "stawy", "wypadanie włosów", "potencja"],
        "bolezni": ["cukrzyca", "ciśnienie", "prostata", "żylaki", "hemoroidy"],
        "gambling": ["kasyno", "sloty", "zakłady", "bonus"],
        "finansy": ["kredyt", "pożyczka", "inwestycja", "zarobek"],
    },
    "nl": {
        "reklama": ["gratis", "korting", "aanbieding", "nu kopen"],
        "nutra": ["afvallen", "gewrichten", "haaruitval", "huid"],
        "bolezni": ["diabetes", "bloeddruk", "prostaat", "spataderen"],
        "gambling": ["casino", "gokkast", "wedden", "bonus"],
        "finansy": ["lening", "investeren", "geld verdienen"],
    },
    "sv": {
        "reklama": ["gratis", "rabatt", "erbjudande", "köp nu"],
        "nutra": ["gå ner i vikt", "leder", "håravfall", "hud"],
        "bolezni": ["diabetes", "blodtryck", "prostata", "åderbråck"],
        "gambling": ["casino", "spelautomat", "betting", "bonus"],
        "finansy": ["lån", "investera", "tjäna pengar"],
    },
    "de": {
        "reklama": ["kostenlos", "rabatt", "angebot", "jetzt kaufen", "gratis versand"],
        "nutra": ["abnehmen", "gelenke", "haarausfall", "potenz", "haut"],
        "bolezni": ["diabetes", "bluthochdruck", "prostata", "krampfadern", "hämorrhoiden"],
        "gambling": ["casino", "spielautomat", "wetten", "bonus"],
        "finansy": ["kredit", "investieren", "geld verdienen", "krypto"],
    },
    "fr": {
        "reklama": ["gratuit", "réduction", "promotion", "acheter", "livraison"],
        "nutra": ["perdre du poids", "articulations", "chute de cheveux", "peau"],
        "bolezni": ["diabète", "tension", "prostate", "varices", "hémorroïdes"],
        "gambling": ["casino", "machine à sous", "paris", "bonus"],
        "finansy": ["crédit", "investir", "gagner de l argent", "crypto"],
    },
    "it": {
        "reklama": ["gratis", "sconto", "offerta", "acquista", "spedizione"],
        "nutra": ["dimagrire", "articolazioni", "caduta capelli", "pelle"],
        "bolezni": ["diabete", "pressione", "prostata", "vene varicose", "emorroidi"],
        "gambling": ["casinò", "slot", "scommesse", "bonus"],
        "finansy": ["prestito", "investire", "guadagnare", "cripto"],
    },
    "es": {
        "reklama": ["gratis", "descuento", "oferta", "comprar", "envío gratis"],
        "nutra": ["adelgazar", "bajar de peso", "articulaciones", "caída del cabello"],
        "bolezni": ["diabetes", "presión arterial", "próstata", "varices", "hemorroides"],
        "gambling": ["casino", "tragamonedas", "apuestas", "bono"],
        "finansy": ["préstamo", "invertir", "ganar dinero", "cripto"],
    },
    "pt": {
        "reklama": ["grátis", "desconto", "promoção", "comprar", "frete grátis"],
        "nutra": ["emagrecer", "perder peso", "articulações", "queda de cabelo"],
        "bolezni": ["diabetes", "pressão alta", "próstata", "varizes", "hemorroidas"],
        "gambling": ["cassino", "caça níqueis", "apostas", "bônus"],
        "finansy": ["empréstimo", "investir", "ganhar dinheiro", "cripto"],
    },
}


def vse_slova():
    """[(слово, язык, категория)] — плоский список для загрузки в базу."""
    out = []
    for yazyk, po_kat in NATIVE.items():
        for kategoriya, slova in po_kat.items():
            for w in slova:
                out.append((w, yazyk, kategoriya))
    return out


if __name__ == "__main__":
    vse = vse_slova()
    po_yaz = {}
    for _, y, _k in vse:
        po_yaz[y] = po_yaz.get(y, 0) + 1
    print(f"языков {len(po_yaz)}, слов {len(vse)}")
    print(", ".join(f"{y}:{n}" for y, n in sorted(po_yaz.items())))
