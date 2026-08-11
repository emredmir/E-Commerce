/* ==========================================================
 * STEP 5 - Preview & Publish
 * ========================================================== */

/* ----------------------------------------------------------
 * Elements & State
 * ---------------------------------------------------------- */
const publishForm = document.getElementById("step5-publish-form");
const publishBtn = document.querySelector(".js-btn-publish");

// Dinamik Varyant Kurgusu İçin State
let parsedVariants = [];
let availableAttributes = {}; // Örn: { "Renk": Set("Siyah", "Beyaz"), "Beden": Set("M", "L") }
let selectedAttributes = {};  // Örn: { "Renk": "Siyah", "Beden": "M" }

/* ----------------------------------------------------------
 * CSRF Token
 * ---------------------------------------------------------- */
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== "") {
        const cookies = document.cookie.split(";");
        for (const cookie of cookies) {
            const c = cookie.trim();
            if (c.startsWith(name + "=")) {
                cookieValue = decodeURIComponent(c.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}
const csrftoken = getCookie("csrftoken");

/* ----------------------------------------------------------
 * Currency Formatter
 * ---------------------------------------------------------- */
function formatMoneyTR(val) {
    if (!val) return val;
    let normalized = String(val).trim();
    
    // Eğer sunucudan sadece virgüllü gelirse (155000,50), parseFloat için noktaya çevirir.
    if (normalized.includes(',') && !normalized.includes('.')) {
        normalized = normalized.replace(',', '.');
    }

    const num = parseFloat(normalized);
    if (isNaN(num)) return val;

    // JavaScript'in yerleşik bölgesel formatlayıcısı 155000.00 sayısını 155.000,00 yapar
    return new Intl.NumberFormat('tr-TR', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    }).format(num);
}

// Ekranda js-price-format olan yerleri sayfa yüklenir yüklenmez estetik hale getirir
function initPriceFormatting() {
    document.querySelectorAll('.js-price-format').forEach(el => {
        const raw = el.getAttribute('data-raw') || el.innerText.trim();
        el.innerText = formatMoneyTR(raw);
    });
}

/* ----------------------------------------------------------
 * 1. Image Gallery Interaction
 * (HTML'deki onclick="changeMainImage(...)" fonksiyonunu global tanımlıyoruz)
 * ---------------------------------------------------------- */
window.changeMainImage = function(url, thumbnailElement) {
    const track = document.querySelector(".js-image-track");
    if (!track || !thumbnailElement) return;

    // Hedeflenen ve şu anki resmin indekslerini al
    const targetIndex = parseInt(thumbnailElement.dataset.index || 0);
    const currentIndex = parseInt(track.dataset.currentIndex || 0);

    // Zaten bu resimdeysek hiçbir şey yapma
    if (targetIndex === currentIndex) return;

    // Uzaklık Hesaplama (Aradaki resim sayısı)
    const diff = Math.abs(targetIndex - currentIndex);
    
    // Geçiş süresini hesapla: Yan resme geçerken 0.4s, aradan çok resim geçecekse hızlanarak akar
    let duration = 0.55; 
    if (diff > 1) {
        duration = Math.min(0.9, 0.55 + (diff * 0.07)); // Max  saniyede tamamlar
    }
    track.style.transitionDuration = `${duration}s`;

    // Track'i X ekseninde yeni resme doğru kaydır (Örn: 2. index için -200% kaydır)
    track.style.transform = `translateX(-${targetIndex * 100}%)`;
    
    // Geçerli index'i güncelle
    track.dataset.currentIndex = targetIndex;

    // Küçük resim (Thumbnail) aktiflik durumunu güncelle
    document.querySelectorAll(".pw-thumbnail-item").forEach(el => el.classList.remove("active"));
    thumbnailElement.classList.add("active");
};

/* ----------------------------------------------------------
 * 2. Dynamic Variant Selector Builder
 * (Aşağıdaki Salt Okunur tablodan veriyi parse edip butonları üretir)
 * ---------------------------------------------------------- */
function buildVariantPreview() {
    const rows = document.querySelectorAll(".js-preview-row");
    const container = document.getElementById("pw-variant-selector-container");
    
    if (!container || rows.length === 0) return;

    parsedVariants = [];
    availableAttributes = {};
    selectedAttributes = {};

    // 1. Tablodan verileri DOM InnerText YERİNE Data attribute'dan oku
    rows.forEach(row => {
        // Güvenli okuma
        const rawAttrs = row.getAttribute('data-attrs') || ""; 
        const priceText = row.getAttribute('data-price') || "0";
        const stockText = row.getAttribute('data-stock') || "0";

        let attrs = {};
        
        // Eğer standart ürün değilse (yani rawAttrs boş değilse), özellikleri parçala
        if (rawAttrs !== "") {
            rawAttrs.split('##').forEach(pair => {
                const parts = pair.split('||'); // Sadece bizim koyduğumuz '||' işaretinden böl
                if (parts.length === 2) {
                    const key = parts[0].trim();
                    const val = parts[1].trim();
                    attrs[key] = val;
                    
                    if (!availableAttributes[key]) {
                        availableAttributes[key] = new Set();
                    }
                    availableAttributes[key].add(val);
                }
            });
        }
        
        parsedVariants.push({
            attrs: attrs,
            price: priceText,
            stock: stockText
        });
    });

    // 2. Eğer varyant özelliği yoksa (Tekil ürün), alanı boş bırak
    if (Object.keys(availableAttributes).length === 0) {
        container.style.display = "none";
        return;
    }

    // 3. Özelliklere göre hap (pill) butonları DOM'a ekle
    container.innerHTML = "";

    // Tablodan "Ana" varyantı bul ve ilk seçili yap --
    let defaultVariantAttrs = {};
    const defaultRow = document.querySelector(".js-preview-row .pw-badge-main")?.closest('.js-preview-row');

    
    if (defaultRow) {
        const rawDefaultAttrs = defaultRow.getAttribute('data-attrs') || "";
        if (rawDefaultAttrs !== "") {
            rawDefaultAttrs.split('##').forEach(pair => {
                const parts = pair.split('||');
                if (parts.length === 2) {
                    defaultVariantAttrs[parts[0].trim()] = parts[1].trim();
                }
            });
        }
    }

    for (const [attrName, valuesSet] of Object.entries(availableAttributes)) {
        
        const groupDiv = document.createElement("div");
        groupDiv.className = "pw-attr-group";
        
        const titleDiv = document.createElement("div");
        titleDiv.className = "pw-attr-title";
        titleDiv.textContent = attrName;
        
        const optionsDiv = document.createElement("div");
        optionsDiv.className = "pw-attr-options";
        
        const valuesArray = Array.from(valuesSet);

        if (defaultVariantAttrs[attrName] && valuesArray.includes(defaultVariantAttrs[attrName])) {
            selectedAttributes[attrName] = defaultVariantAttrs[attrName];
        } else {
            selectedAttributes[attrName] = valuesArray[0];
        }
        
        valuesArray.forEach(val => {
            const pill = document.createElement("div");
            pill.className = "pw-attr-pill";
            if (val === selectedAttributes[attrName]) {
                pill.classList.add("active");
            }
            pill.textContent = val;
            
            // Hap butona tıklanınca
            pill.addEventListener("click", () => {
                // Aynı grubun aktif sınıfını temizle
                Array.from(optionsDiv.children).forEach(c => c.classList.remove("active"));
                pill.classList.add("active");
                
                // Seçimi güncelle ve fiyat/stok bilgisini render et
                selectedAttributes[attrName] = val;
                updatePreviewPricing();
            });
            
            optionsDiv.appendChild(pill);
        });
        
        groupDiv.appendChild(titleDiv);
        groupDiv.appendChild(optionsDiv);
        container.appendChild(groupDiv);
    }

    // Tablo okunduktan sonra ilk fiyatı hesapla
    updatePreviewPricing();
}

function updatePreviewPricing() {
    // Seçili özelliklere tam uyan varyantı bul
    const matchedVariant = parsedVariants.find(v => {
        for (const [key, val] of Object.entries(selectedAttributes)) {
            if (v.attrs[key] !== val) return false;
        }
        return true;
    });

    const priceEl = document.getElementById("pw-preview-price");
    const stockEl = document.getElementById("pw-preview-stock");
    
    if (matchedVariant) {
        if (priceEl) priceEl.innerHTML = `₺${formatMoneyTR(matchedVariant.price)}`;
        
        if (stockEl) {
            stockEl.innerHTML = `Stok: ${matchedVariant.stock} adet`;
            // Stok durumuna göre renk değiştir
            if (parseInt(matchedVariant.stock) === 0) {
                stockEl.style.color = "var(--red)";
                stockEl.style.backgroundColor = "#fef2f2";
                stockEl.style.borderColor = "#fecaca";
                stockEl.innerHTML = `Tükendi`;
            } else {
                stockEl.style.color = "var(--green-700)";
                stockEl.style.backgroundColor = "var(--green-50)";
                stockEl.style.borderColor = "#bbf7d0";
            }
        }
    } else {
        // Eğer bu kombinasyon satıcı tarafından oluşturulmamışsa
        if (priceEl) priceEl.innerHTML = `<span style="font-size: 0.9rem; color: var(--gray-400);">
        Bu Varyant Mevcut Değil<br>
        <small>Müşteri görünümünde bu varyant gösterilmeyecek.</small></span>`;
        if (stockEl) stockEl.innerHTML = "-";
    }

    // Varyanta tıklandığında ona ait kapak resmini bul ve değiştir
    updateMainImageByAttributes();

}
// Seçili özelliklere uyan resmi bulur
function updateMainImageByAttributes() {
    const thumbnails = document.querySelectorAll(".pw-thumbnail-item");
    const track = document.querySelector(".js-image-track");

    // Slayt alanındaki büyük resimleri alıyoruz
    const trackItems = track ? track.children : [];

    let matchedThumb = null; // Varyanta özel resim
    let commonThumb = null;  // Ortak (genel) resim
    let visibleIndex = 0;    // Ekranda görünecek resimlerin yeni sırası
    
    thumbnails.forEach((thumb, originalIndex) => {
        const attrs = thumb.dataset.attrs || "";
        let isMatch = true;
        let isCommon = !attrs; // Verisi yoksa ortak gruptur
        
        if (!isCommon) {
            const thumbAttrPairs = attrs.split(',');
            for (let pair of thumbAttrPairs) {
                let [key, val] = pair.split(':');
                if (key && val && selectedAttributes[key] !== val) {
                    isMatch = false;
                    break;
                }
            }
        }
        
        const trackItem = trackItems[originalIndex];

        // 1. Farklı varyant resimlerini gizle, uyanları göster ve index'i güncelle
        if (isMatch || isCommon) {
            thumb.style.display = ""; // Küçük resmi göster
            if (trackItem) trackItem.style.display = ""; // Slayttaki büyük resmi göster
            
            // Kaydırma (translateX) matematiğinin gizlenen resimlerden etkilenmemesi için index'i yeniden veriyoruz
            thumb.dataset.index = visibleIndex;
            
            if (isMatch && !isCommon && !matchedThumb) matchedThumb = thumb;
            if (isCommon && !commonThumb) commonThumb = thumb;
            
            visibleIndex++;
        } else {
            thumb.style.display = "none"; // İlgisiz varyantı gizle
            thumb.classList.remove("active"); // GİZLENENLERİN AKTİFLİĞİNİ KESİNLİKLE AL
            if (trackItem) trackItem.style.display = "none"; // Slayttakini de gizle
        }
    });

    // 2. Gösterilecek ilk resme (hedefe) git
    let targetThumb = matchedThumb || commonThumb;

    if (targetThumb) {
        const targetUrl = targetThumb.querySelector("img").src;
        
        // Varyant değiştiğinde 'aynı sıradaki' resme denk gelirse early-return (erken çıkış) 
        // yapmasını engellemek için mevcut index'i anlık sıfırlıyoruz.
        if (track) track.dataset.currentIndex = "-1";
        
        changeMainImage(targetUrl, targetThumb);
        
        // Mobilde vs. küçük resim alanını seçili olana kaydır
        targetThumb.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }
}

/* ----------------------------------------------------------
 * 3. Final Publish (Yayınlama) İşlemi
 * ---------------------------------------------------------- */
publishForm?.addEventListener("submit", async (e) => {
    e.preventDefault();

    if (!publishBtn) return;

    // SweetAlert2 ile Final Onay Pop-up'ı
    const confirmed = await Swal.fire({
        title: 'Ürünü Yayınla',
        html: `
            <div>
                Bu ürün mağazanızda satışa açılacak ve
                müşteriler tarafından görüntülenebilir hale gelecektir.
                <br><br>
                <strong>Bu işlemi gerçekleştirmek istiyor musunuz?</strong>
            </div>
        `,
        icon: 'info',
        showCancelButton: true,
        confirmButtonColor: 'var(--teal-600)',
        cancelButtonColor: 'var(--gray-400)',
        confirmButtonText: '<i style="margin-right:8px;"></i>Ürünü Yayınla',
        cancelButtonText: 'Vazgeç',
        reverseButtons: true,
        focusCancel: true
    });

    if (!confirmed.isConfirmed) return;

    WizardUI.setButtonLoading(publishBtn, true, "Yayına Alınıyor...");

    try {
        const url = publishForm.action;
        const formData = new FormData(publishForm);

        const inlineDecision = document.querySelector('input[name="inline_publish_decision"]:checked');
        if (inlineDecision) {
            formData.append("decision", inlineDecision.value);
            if (inlineDecision.value === "use_existing") {
                const matchedId = document.getElementById("inline_matched_product_id").value;
                formData.append("product_id", matchedId);
            }
        }

        const response = await fetch(url, {
            method: "POST",
            body: formData,
            headers: {
                "X-CSRFToken": csrftoken,
                "X-Requested-With": "XMLHttpRequest",
            }
        });

        let data = {};
        try { data = await response.json(); } catch(err) {}

        if (!response.ok || !data.success) {

            // YENİ BİR EŞLEŞME BULDUYSA (Son saniye çakışması)
            // İkinci pop-up (Güvenlik Ağı) sadece bu senaryoda tetiklenir.
            if (data.duplicate) {
            
                WizardUI.setButtonLoading(
                    publishBtn,
                    false
                );
            
                handleDuplicateProduct(data);
            
                return;
            }
        
        
            WizardUI.showToast(
                "error",
                data.message || "Yayınlama sırasında beklenmeyen bir hata oluştu."
            );
        
            WizardUI.setButtonLoading(
                publishBtn,
                false
            );
        
            return;
        }

        // Başarılıysa görkemli bir SweetAlert Success göster
        await Swal.fire({
            title: 'Tebrikler!',
            text: data.message || 'Ürün başarıyla yayına alındı.',
            icon: 'success',
            confirmButtonColor: 'var(--teal-600)',
            confirmButtonText: 'Ürüne Git'
        });

        // Backend'den gelen ürün detay / liste sayfasına yönlendir
        if (data.redirect_url) {
            window.location.href = data.redirect_url;
        }

    } catch (error) {
        console.error("Publish Error:", error);
        WizardUI.showToast("error", "Sunucu ile iletişim kurulamadı. Lütfen bağlantınızı kontrol edin.");
        WizardUI.setButtonLoading(publishBtn, false);
    }
});

/* ----------------------------------------------------------
 * Kargo Geri Sayım Sayaç Mantığı (Saat 21.00 Bazlı)
 * ---------------------------------------------------------- */
function initShippingCountdown() {
    const timerEl = document.getElementById("js-shipping-timer");
    if (!timerEl) return;

    function updateTimer() {
        // Türkiye saatine göre (UTC+3) anlık zamanı al
        const now = new Date();
        const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
        const trTime = new Date(utc + (3600000 * 3)); // TR Saati

        // Bugün saat 21.00
        let target = new Date(trTime);
        target.setHours(21, 0, 0, 0);

        // Eğer saat 21.00 geçtiyse hedef yarın saat 21.00 olur
        if (trTime >= target) {
            target.setDate(target.getDate() + 1);
        }

        const diffMs = target - trTime;
        const diffHours = Math.floor((diffMs % 86400000) / 3600000);
        const diffMins = Math.floor((diffMs % 3600000) / 60000);

        timerEl.textContent = `${diffHours} saat ${diffMins} dk içinde sipariş verirsen yarın kargoda!`;
    }

    updateTimer();
    setInterval(updateTimer, 60000); // Her 1 dakikada bir güncelle
}

async function handleDuplicateProduct(data) {

    const product = data.match;

    if (!product) {
        WizardUI.showToast(
            "error",
            "Benzer ürün bilgisi alınamadı."
        );

        WizardUI.setButtonLoading(
            publishBtn,
            false
        );

        return;
    }


    const result = await Swal.fire({

        title: "Benzer ürün bulundu",

        html: `
            <div style="text-align:left">

                <p>
                Sistemde bu ürüne çok benzeyen
                mevcut bir katalog ürünü bulundu.
                </p>

                <hr>

                <strong>${product.name}</strong>

                <br>

                Marka:
                ${product.brand || "-"}

                <br>

                Kategori:
                ${product.category}

                <br><br>

                Bu ürünün üzerine teklif olarak eklemek ister misiniz?

            </div>
        `,

        icon:"warning",

        showCancelButton:true,

        confirmButtonText:
            "Mevcut ürüne ekle",

        cancelButtonText:
            "Yeni ürün oluştur",

    });


    const formData = new FormData(
        publishForm
    );


    if(result.isConfirmed){
        formData.append("decision", "use_existing");
        formData.append("product_id", product.id);
        publishExisting(formData);
    }
    else if(result.dismiss === Swal.DismissReason.cancel){
        // Sadece ve sadece "Yeni ürün oluştur" (Cancel) butonuna tıklandıysa
        formData.append("decision", "create_new");
        publishExisting(formData);
    } else {
        // ESC'ye basıldı veya dışarı tıklandı (İşlemi iptal et)
        WizardUI.setButtonLoading(publishBtn, false);
    }

}

async function publishExisting(formData){

    WizardUI.setButtonLoading(
        publishBtn,
        true,
        "Ekleniyor..."
    );

    const data = await sendPublishRequest(
        formData
    );


    if(!data.success){

        WizardUI.showToast(
            "error",
            data.message || "Hata oluştu."
        );

        WizardUI.setButtonLoading(
            publishBtn,
            false
        );

        return;
    }


    await Swal.fire({
        title:"Başarılı!",
        text:data.message,
        icon:"success",
        confirmButtonText:"Devam"
    });


    if(data.redirect_url){

        window.location.href =
            data.redirect_url;

    }
}

async function sendPublishRequest(formData){

    try {

        const response = await fetch(
            publishForm.action,
            {
                method:"POST",
                body:formData,
                headers:{
                    "X-CSRFToken": csrftoken,
                    "X-Requested-With":"XMLHttpRequest",
                }
            }
        );


        const data = await response.json();

        return data;


    } catch(error){

        console.error(error);

        return {
            success:false,
            message:
            "Sunucu ile bağlantı kurulamadı."
        };
    }
}


/* ----------------------------------------------------------
 * Init
 * ---------------------------------------------------------- */
document.addEventListener("DOMContentLoaded", () => {
    // fiyatların formatını (155.000,00) çevir
    initPriceFormatting();

    // Sayfa yüklendiğinde varyant butonlarını (pills) oluştur
    buildVariantPreview();

    // TIKLAMA YERİNE ÜZERİNE GELİNCE (HOVER) ÇALIŞMASI İÇİN:
    document.querySelectorAll(".pw-thumbnail-item").forEach(thumb => {
        const imgUrl = thumb.querySelector("img").src;
        thumb.addEventListener("mouseenter", () => {
            changeMainImage(imgUrl, thumb);
        });
    });

    initShippingCountdown();

    
});