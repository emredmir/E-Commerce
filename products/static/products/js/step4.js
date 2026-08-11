/* ==========================================================
 * STEP 4 - Sales & Inventory Management
 * ========================================================== */

/* ----------------------------------------------------------
 * Elements & State
 * ---------------------------------------------------------- */
const saveForm = document.getElementById("step4-form");
const completeForm = document.getElementById("step4-complete-form");
const completeBtn = document.querySelector(".js-btn-complete");

/* Toplu İşlem (Bulk) Elementleri */
const bulkPriceInput = document.querySelector(".js-bulk-price");
const bulkStockInput = document.querySelector(".js-bulk-stock");
const bulkSkuInput = document.querySelector(".js-bulk-sku");
const btnBulkApply = document.querySelector(".js-btn-bulk-apply");
const btnGenerateSku = document.querySelector(".js-generate-sku-prefix");

/* ----------------------------------------------------------
 * CSRF
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
 * 1. Default Variant Radio Senkronizasyonu
 * (Radyo butonu değiştiğinde gizli checkbox'ı senkronize eder)
 * ---------------------------------------------------------- */
document.querySelectorAll(".js-default-radio").forEach((radio) => {
    radio.addEventListener("change", (e) => {
        // 1. Tablodaki tüm is_default checkbox'larını uncheck yap
        document.querySelectorAll('input[type="checkbox"][name$="-is_default"]').forEach((cb) => {
            cb.checked = false;
        });

        // 2. Sadece tıklanan radyo butonunun yanındaki checkbox'ı check yap
        const hiddenCheckbox = e.target.closest("td").querySelector('input[type="checkbox"]');
        if (hiddenCheckbox) {
            hiddenCheckbox.checked = true;
        }
    });
});

/* ----------------------------------------------------------
 * 2. Sihirli SKU Öneki Üretici (Magic Button)
 * ---------------------------------------------------------- */
const skuPreviewContainer = document.querySelector(".js-sku-preview");
const skuPreviewSpan = skuPreviewContainer?.querySelector("span");

// Kullanıcının manuel mi yazdığı yoksa sihirli butonu mu kullandığı bilgisi
let isMagicPrefix = false;

function generateSkuPart(text, maxLength = 3) {
    if (!text) return "";
    let cleaned = text.replace(/[^a-zA-Z0-9\s]/g, "").trim().toUpperCase();
    cleaned = cleaned.replace(/Ğ/g, "G").replace(/Ü/g, "U").replace(/Ş/g, "S")
                     .replace(/I/g, "I").replace(/İ/g, "I").replace(/Ö/g, "O")
                     .replace(/Ç/g, "C");
    return cleaned.replace(/\s+/g, "").substring(0, maxLength);
}

// Canlı Önizlemeyi Güncelleyen Fonksiyon
function updateSkuPreview() {
    if (!skuPreviewContainer || !skuPreviewSpan) return;
    
    let baseVal = bulkSkuInput.value.trim();
    if (!baseVal) {
        skuPreviewContainer.style.display = "none";
        return;
    }

    let exampleSku = baseVal;

    // SADECE sihirli butonla üretildiyse araya varyant kısaltması ekle
    if (isMagicPrefix) {
        const firstRow = document.querySelector(".js-variant-row");
        let attrPrefix = "";

        if (firstRow) {
            const rawAttrs = firstRow.dataset.attributes || "";
            if (rawAttrs) {
                const attrsArray = rawAttrs.split("-");
                attrPrefix = attrsArray.map(a => generateSkuPart(a, 3)).join("-");
            }
        }

        if (!exampleSku.endsWith("-")) exampleSku += "-";
        if (attrPrefix) exampleSku += attrPrefix + "-";
        exampleSku += "001";
    } else {
        // Kullanıcı kendi yazıyorsa sadece sonuna sıra numarası ekleneceğini göster
        if (!exampleSku.endsWith("-")) exampleSku += "-";
        exampleSku += "001";
    }

    skuPreviewSpan.textContent = exampleSku;
    skuPreviewContainer.style.display = "block";
}

// Kullanıcı klavyeyle müdahale ederse "Magic Mode" devreden çıkar
bulkSkuInput?.addEventListener("input", () => {
    isMagicPrefix = false; // Kullanıcı kendi eliyle yazıyor!
    updateSkuPreview();
});

// Sihirli Butona basıldığında
btnGenerateSku?.addEventListener("click", () => {
    const productName = btnGenerateSku.dataset.productName || "";
    if (!productName) return;

    // Ürün adının ilk 3 kelimesinden 3'er harf al
    const words = productName.trim().split(/\s+/).slice(0, 3);
    const prefixParts = words.map(w => generateSkuPart(w, 3));

    let generatedPrefix = prefixParts.filter(p => p.length > 0).join("-");
    
    if (generatedPrefix) {
        isMagicPrefix = true;
        bulkSkuInput.value = generatedPrefix + "-";

        updateSkuPreview();
        
        // Kullanıcıya küçük bir animasyonla bildirim
        btnGenerateSku.style.transform = "scale(1.2)";
        setTimeout(() => btnGenerateSku.style.transform = "scale(1)", 200);
    }
});

/* ----------------------------------------------------------
 * 3. Toplu İşlem (Bulk Apply)
 * ---------------------------------------------------------- */
btnBulkApply?.addEventListener("click", () => {
    // Kopyalamadan önce fiyatı kesin olarak X.XXX,YY formatına sok
    if (bulkPriceInput.value) {
        handlePriceBlur({ target: bulkPriceInput });
    }

    const priceVal = bulkPriceInput.value.trim();
    const stockVal = bulkStockInput.value.trim();
    const skuVal = bulkSkuInput.value.trim();
    
    // Hangi radyo buton seçili: 'empty' (sadece boşlar) veya 'all' (tümü)
    const mode = document.querySelector('input[name="bulk_mode"]:checked').value; 

    let appliedCount = 0;
    const rows = document.querySelectorAll(".js-variant-row");

    rows.forEach((row, index) => {
        const pInput = row.querySelector(".js-input-price");
        const sInput = row.querySelector(".js-input-stock");
        const kInput = row.querySelector(".js-input-sku");

        // Fiyat Uygulama
        if (priceVal && (mode === "all" || !pInput.value)) {
            pInput.value = priceVal;
            appliedCount++;
        }

        // Stok Uygulama
        if (stockVal && (mode === "all" || !sInput.value)) {
            sInput.value = stockVal;
            appliedCount++;
        }

        // SKU Uygulama (Akıllı Değişken ve Sayaç)
        if (skuVal && (mode === "all" || !kInput.value)) {
            let finalSku = skuVal;

            if (isMagicPrefix) {
                // 1- SİHİRLİ MOD: [Önek]-[VaryantKısaltmaları]-[001]
                const rawAttrs = row.dataset.attributes || "";
                let attrPrefix = "";
                
                if (rawAttrs) {
                    const attrsArray = rawAttrs.split("-");
                    attrPrefix = attrsArray.map(a => generateSkuPart(a, 3)).join("-");
                }
                
                if (!finalSku.endsWith("-")) finalSku += "-";
                if (attrPrefix) finalSku += attrPrefix + "-";
                finalSku += (index + 1).toString().padStart(3, "0");

            } else {
                // 2- MANUEL MOD: Kullanıcının yazdığı kod + [Sıra No]
                // Varyant isimlerini araya SIKIŞTIRMAZ.
                if (!finalSku.endsWith("-")) finalSku += "-";
                finalSku += (index + 1).toString().padStart(3, "0");
            }
            
            kInput.value = finalSku;
            appliedCount++;
        }
    });

    if (appliedCount > 0) {
        WizardUI.showToast("success", "Toplu değerler tabloya uygulandı.");
    } else {
        WizardUI.showToast("info", "Uygulanacak boş alan bulunamadı.");
    }
});

/* ----------------------------------------------------------
 * 4. Frontend Tablo Doğrulaması (Client-Side Validation)
 * ---------------------------------------------------------- */
function clearErrors() {
    document.querySelectorAll(".is-invalid").forEach(el => el.classList.remove("is-invalid"));
    document.querySelectorAll(".pw-error-text").forEach(el => el.style.display = "none");
}

function showError(input, message) {
    if (!input) return;
    input.classList.add("is-invalid");
    const errorDiv = input.closest(".pw-input-wrapper")?.querySelector(".pw-error-text");
    if (errorDiv) {
        errorDiv.textContent = message;
        errorDiv.style.display = "block";
    }
}

function validateTable() {
    clearErrors();
    let isValid = true;
    let hasDefault = false;

    const seenSkus = new Set();
    const seenBarcodes = new Set();

    // 1. Varsayılan Seçimi Kontrolü
    document.querySelectorAll(".js-default-radio").forEach(r => {
        if (r.checked) hasDefault = true;
    });

    if (!hasDefault) {
        WizardUI.showToast("error", "Lütfen ürünün ana (varsayılan) varyantını seçin.");
        isValid = false;
    }

    // 2. Tablo Satırları Kontrolü
    document.querySelectorAll(".js-variant-row").forEach(row => {
        const pInput = row.querySelector(".js-input-price");
        const sInput = row.querySelector(".js-input-stock");
        const kInput = row.querySelector(".js-input-sku");
        const bInput = row.querySelector(".js-input-barcode");

        // Fiyat kontrolü
        let cleanPrice = pInput.value.replace(/\./g, '').replace(',', '.'); // 1.000,50 -> 1000.50 formatına geri döndür
        if (!cleanPrice || parseFloat(cleanPrice) <= 0) {
            showError(pInput, "Geçerli fiyat girin.");
            isValid = false;
        }

        // Stok kontrolü
        if (!sInput.value || parseInt(sInput.value) < 0) {
            showError(sInput, "Geçerli stok girin.");
            isValid = false;
        }

        // SKU Benzersizlik kontrolü (Aynı tablo içi)
        if (kInput.value) {
            const val = kInput.value.trim().toLowerCase();
            if (seenSkus.has(val)) {
                showError(kInput, "Bu SKU tabloda tekrar ediyor.");
                isValid = false;
            }
            seenSkus.add(val);
        }

        // Barkod Benzersizlik kontrolü (Aynı tablo içi)
        if (bInput.value) {
            const val = bInput.value.trim();
            if (seenBarcodes.has(val)) {
                showError(bInput, "Bu barkod tabloda tekrar ediyor.");
                isValid = false;
            }
            seenBarcodes.add(val);
        }
    });

    if (!isValid) {
        WizardUI.showToast("error", "Lütfen tablodaki hataları düzeltin.");
    }

    return isValid;
}

/* ----------------------------------------------------------
 * 5. Zincirleme Kayıt İşlemi (Save -> Complete -> Redirect)
 * ---------------------------------------------------------- */
completeForm?.addEventListener("submit", async (e) => {
    e.preventDefault();

    if (!completeBtn || !saveForm) return;

    // 1. Önce tabloda eksik veri var mı diye Client-Side bak
    if (!validateTable()) {
        return; 
    }

    WizardUI.setButtonLoading(completeBtn, true, "Kaydediliyor...");

    try {
        // AŞAMA 1: Tablodaki Verileri Backend'e Kaydet (AJAX)
        const saveUrl = saveForm.dataset.saveUrl;
        const formData = new FormData(saveForm);

        // Formatlanmış UI değerlerini Backend standartına (1.000,50 -> 1000.50) temizle
        document.querySelectorAll(".js-input-price").forEach(input => {
            let cleanVal = input.value.replace(/\./g, '').replace(',', '.');
            formData.set(input.name, cleanVal);
        });

        const saveResponse = await fetch(saveUrl, {
            method: "POST",
            body: formData,
            headers: {
                "X-CSRFToken": csrftoken,
                "X-Requested-With": "XMLHttpRequest",
            }
        });

        let saveData = {};
        try { saveData = await saveResponse.json(); } catch(err) {}

        if (!saveResponse.ok || !saveData.success) {
            // Eğer Backend spesifik alan hataları yolladıysa
            if (saveData.errors) {
                for (const [prefix, fieldErrors] of Object.entries(saveData.errors)) {
                    for (const [fieldName, msgs] of Object.entries(fieldErrors)) {
                        // Örn input name: "variant_1-price"
                        const inputName = `${prefix}-${fieldName}`;
                        const inputNode = document.querySelector(`[name="${inputName}"]`);
                        if (inputNode) showError(inputNode, msgs[0]);
                    }
                }
            }
            WizardUI.showToast("error", saveData.message || "Kaydetme sırasında hata oluştu.");
            WizardUI.setButtonLoading(completeBtn, false);
            return;
        }

        // AŞAMA 2: Adımı Tamamla ve Sonraki Sayfaya Zıpla (AJAX)
        const compUrl = completeForm.action;
        const compFormData = new FormData(completeForm);

        const compResponse = await fetch(compUrl, {
            method: "POST",
            body: compFormData,
            headers: {
                "X-CSRFToken": csrftoken,
                "X-Requested-With": "XMLHttpRequest",
            }
        });

        let compData = {};
        try { compData = await compResponse.json(); } catch(err) {}

        if (!compResponse.ok || !compData.success) {
            WizardUI.showToast("error", compData.message || "Adım tamamlanamadı.");
            WizardUI.setButtonLoading(completeBtn, false);
            return;
        }

        // Her şey kusursuz, Step 5'e (Yayınlama Ekranı) yönlendir.
        if (compData.redirect_url) {
            window.location.href = compData.redirect_url;
        }

    } catch (error) {
        console.error("Fatal Error during Step 4 submission:", error);
        WizardUI.showToast("error", "Sunucu ile iletişim kurulamadı.");
        WizardUI.setButtonLoading(completeBtn, false);
    }
});

/* ----------------------------------------------------------
 * 6. Gerçek Zamanlı Para Birimi Formatlayıcı (Örn: 1000 -> 1.000,50)
 * ---------------------------------------------------------- */
function formatRawToTR(val) {
    let parts = val.split(',');
    let integerPart = parts[0].replace(/\./g, '');
    let decimalPart = parts.length > 1 ? ',' + parts[1] : '';

    if (integerPart !== '') {
        integerPart = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    }
    return integerPart + decimalPart;
}

function formatPriceField(e) {
    let input = e.target;
    let cursorPosition = input.selectionStart; // Kullanıcının imlecini takip et
    let oldLength = input.value.length;

    // Sadece rakam, nokta ve virgüle izin ver
    let val = input.value.replace(/[^0-9.,]/g, '');

    // Birden fazla virgül varsa sadece ilkini baz al
    let parts = val.split(',');
    if (parts.length > 2) {
        val = parts[0] + ',' + parts.slice(1).join('').replace(/,/g, '');
        parts = val.split(',');
    }

    let integerPart = parts.length > 0 ? parts[0] : '';
    let decimalPart = parts.length > 1 ? ',' + parts[1] : '';

    // Integer kısımdaki eski noktaları sil
    integerPart = integerPart.replace(/\./g, '');
    
    // "05" gibi baştaki gereksiz sıfırları temizle
    if (integerPart.length > 1 && integerPart.startsWith('0')) {
        integerPart = parseInt(integerPart, 10).toString();
    }

    // Yeni binlik ayraçları (.) ekle
    if (integerPart !== '') {
        integerPart = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    }

    let newValue = integerPart + decimalPart;
    input.value = newValue;

    // İmleci doğru yerde tut (zıplamasını engelle)
    let newLength = newValue.length;
    cursorPosition = cursorPosition + (newLength - oldLength);
    if (cursorPosition < 0) cursorPosition = 0;
    
    try { input.setSelectionRange(cursorPosition, cursorPosition); } catch(err) {}
}

// Inputtan çıkıldığında (blur) veya dışarıdan değer atandığında sonuna ,00 veya ,50 formatını kesinkes oturtan fonksiyon.
function handlePriceBlur(e) {
    let input = e.target;
    let val = String(input.value);
    if (!val) return;

    // Tüm noktaları (binlik ayırıcıları) ve rakam/virgül harici şeyleri sil
    let cleanVal = val.replace(/\./g, '').replace(/[^0-9,]/g, '');
    let parts = cleanVal.split(',');
    
    // Tam sayı (lira) kısmını al
    let integerPart = parts[0] || '0';
    integerPart = parseInt(integerPart, 10).toString(); // Baştaki gereksiz 0'ları siler
    if (integerPart === 'NaN') integerPart = '0';
    
    // Ondalık (kuruş) kısmını al ve düzelt
    let decimalPart = parts.length > 1 ? parts[1] : '';
    if (decimalPart.length === 0) decimalPart = '00';
    else if (decimalPart.length === 1) decimalPart += '0';
    else decimalPart = decimalPart.substring(0, 2);

    // Tekrar X.XXX,YY formatında birleştir ve yansıt
    let formattedValue = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".") + ',' + decimalPart;
    input.value = formattedValue;
}

document.addEventListener("DOMContentLoaded", () => {
    // --- 1. STOK ALANLARINI TEMİZLE ---
    const stockInputs = document.querySelectorAll('.js-input-stock, .js-bulk-stock');
    stockInputs.forEach(input => {
        // Django'dan default 0 geldiyse input'u boşalt
        if (input.value === "0") {
            input.value = "";
        }
    });

    // --- 2. FİYAT ALANLARINI TEMİZLE VE FORMATLA ---
    const priceInputs = document.querySelectorAll('.js-input-price, .js-bulk-price');
    
    priceInputs.forEach(input => {
        // Django'dan default 0, 0.00 veya 0,00 geldiyse input'u boşalt
        let val = String(input.value).trim();
        if (val === "0" || val === "0.00" || val === "0,00") {
            input.value = "";
        }

        // İlk açılışta Django'dan gelen GERÇEK bir sayı varsa (örn: 1000.50), TR formatına (1.000,50) çevir
        if (input.value) {
            val = String(input.value);
            // Django ondalık için nokta koyar. Eğer sayı sadece virgül olmadan nokta içeriyorsa, noktayı virgüle çevir.
            if (val.includes('.') && !val.includes(',')) {
                val = val.replace('.', ',');
            }
            input.value = val;

            // Sayfa yüklendiği gibi katı formata (,00 veya ,50) zorla
            handlePriceBlur({ target: input });
        }

        // Kullanıcı klavyede her tuşa bastığında anında formata sok
        input.addEventListener('input', formatPriceField);
        // Kullanıcı başka yere tıkladığında veya alandan çıktığında katı format (örnek: 1000 yazdıysa 1.000,00 yapar)
        input.addEventListener('blur', handlePriceBlur);

    });
});