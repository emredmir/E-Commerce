import re
import unicodedata


def normalize_product_name(name):
    """
    Ürün adını standart hale getirir.

    Dönen değerler:

    normalized_name:
        Görsel normalize edilmiş isim

    normalized_key:
        Kesin karşılaştırma anahtarı

    tokens:
        Benzerlik ve arama için kelime listesi


    Örnek:

        Apple iPhone-15 128GB

    Sonuç:

        normalized_name:
            apple iphone 15 128gb

        normalized_key:
            appleiphone15128gb

        tokens:
            [
                "apple",
                "iphone",
                "15",
                "128gb"
            ]
    """


    if not name:
        return {
            "normalized_name": "",
            "normalized_key": "",
            "tokens": [],
        }


    #
    # Unicode normalize
    #
    name = unicodedata.normalize(
        "NFKC",
        name,
    )


    #
    # Büyük/küçük harf normalize
    #
    name = name.casefold()


    #
    # Ayırıcıları boşluk yap
    #
    name = re.sub(
        r"[-_/]+",
        " ",
        name,
    )


    #
    # Noktalama karakterlerini kaldır
    #
    name = re.sub(
        r"[^\w\s]",
        "",
        name,
    )


    #
    # Çoklu boşlukları temizle
    #
    name = re.sub(
        r"\s+",
        " ",
        name,
    ).strip()


    normalized_name = name


    #
    # Token oluştur
    #
    tokens = normalized_name.split()


    #
    # Karşılaştırma anahtarı
    #
    normalized_key = re.sub(
        r"\W+",
        "",
        normalized_name,
    )


    return {
        "normalized_name": normalized_name,

        "normalized_key": normalized_key,

        "tokens": tokens,
    }