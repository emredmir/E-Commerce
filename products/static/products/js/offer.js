/* ==========================================================
 * OFFER WIZARD (Mevcut Ürüne Teklif Ekleme) JS
 * ========================================================== */

document.addEventListener("DOMContentLoaded", () => {

    /* ----------------------------------------------------------
     * 1. Araçlar (Utils)
     * ---------------------------------------------------------- */
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== "") {
            const cookies = document.cookie.split(";");
            for (let cookie of cookies) {
                cookie = cookie.trim();
                if (cookie.startsWith(name + "=")) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
    const csrftoken = getCookie("csrftoken");

    /* ----------------------------------------------------------
     * 2. Sihirli SKU Öneki Üretici (Magic Button)
     * ---------------------------------------------------------- */
    const bulkSkuInput = document.querySelector(".js-bulk-sku");
    const btnGenerateSku = document.querySelector(".js-generate-sku-prefix");
    const skuPreviewContainer = document.querySelector(".js-sku-preview");
    const skuPreviewSpan = skuPreviewContainer?.querySelector("span");

    let isMagicPrefix = false;

    function generateSkuPart(text, maxLength = 3) {
        if (!text) return "";
        let cleaned = text.replace(/[^a-zA-Z0-9\s]/g, "").trim().toUpperCase();
        cleaned = cleaned.replace(/Ğ/g, "G").replace(/Ü/g, "U").replace(/Ş/g, "S")
                         .replace(/I/g, "I").replace(/İ/g, "I").replace(/Ö/g, "O")
                         .replace(/Ç/g, "C");
        return cleaned.replace(/\s+/g, "").substring(0, maxLength);
    }

    function updateSkuPreview() {
        if (!skuPreviewContainer || !skuPreviewSpan) return;
        
        let baseVal = bulkSkuInput.value.trim();
        if (!baseVal) {
            skuPreviewContainer.style.display = "none";
            return;
        }

        let exampleSku = baseVal;

        if (isMagicPrefix) {
            const firstRow = document.querySelector(".js-offer-row");
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
            if (!exampleSku.endsWith("-")) exampleSku += "-";
            exampleSku += "001";
        }

        skuPreviewSpan.textContent = exampleSku;
        skuPreviewContainer.style.display = "block";
    }

    bulkSkuInput?.addEventListener("input", () => {
        isMagicPrefix = false; 
        updateSkuPreview();
    });

    btnGenerateSku?.addEventListener("click", () => {
        const productName = btnGenerateSku.dataset.productName || "";
        if (!productName) return;

        const words = productName.trim().split(/\s+/).slice(0, 3);
        const prefixParts = words.map(w => generateSkuPart(w, 3));

        let generatedPrefix = prefixParts.filter(p => p.length > 0).join("-");
        
        if (generatedPrefix) {
            isMagicPrefix = true;
            bulkSkuInput.value = generatedPrefix + "-";
            updateSkuPreview();
            
            btnGenerateSku.style.transform = "scale(1.2)";
            setTimeout(() => btnGenerateSku.style.transform = "scale(1)", 200);
        }
    });

    /* ----------------------------------------------------------
     * 3. Para Birimi Formatlayıcı (1.000,50)
     * ---------------------------------------------------------- */
    function formatPriceField(e) {
        let input = e.target;
        let cursorPosition = input.selectionStart; 
        let oldLength = input.value.length;

        let val = input.value.replace(/[^0-9.,]/g, '');

        let parts = val.split(',');
        if (parts.length > 2) {
            val = parts[0] + ',' + parts.slice(1).join('').replace(/,/g, '');
            parts = val.split(',');
        }

        let integerPart = parts.length > 0 ? parts[0] : '';
        let decimalPart = parts.length > 1 ? ',' + parts[1] : '';

        integerPart = integerPart.replace(/\./g, '');
        
        if (integerPart.length > 1 && integerPart.startsWith('0')) {
            integerPart = parseInt(integerPart, 10).toString();
        }

        if (integerPart !== '') {
            integerPart = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
        }

        let newValue = integerPart + decimalPart;
        input.value = newValue;

        let newLength = newValue.length;
        cursorPosition = cursorPosition + (newLength - oldLength);
        if (cursorPosition < 0) cursorPosition = 0;
        
        try { input.setSelectionRange(cursorPosition, cursorPosition); } catch(err) {}
    }

    function handlePriceBlur(e) {
        let input = e.target;
        let val = String(input.value);
        if (!val) return;

        let cleanVal = val.replace(/\./g, '').replace(/[^0-9,]/g, '');
        let parts = cleanVal.split(',');
        
        let integerPart = parts[0] || '0';
        integerPart = parseInt(integerPart, 10).toString(); 
        if (integerPart === 'NaN') integerPart = '0';
        
        let decimalPart = parts.length > 1 ? parts[1] : '';
        if (decimalPart.length === 0) decimalPart = '00';
        else if (decimalPart.length === 1) decimalPart += '0';
        else decimalPart = decimalPart.substring(0, 2);

        let formattedValue = integerPart.replace(/\B(?=(\d{3})+(?!\d))/g, ".") + ',' + decimalPart;
        input.value = formattedValue;
    }

    // Inputları temizle ve formata sok
    const stockInputs = document.querySelectorAll('.js-input-stock, .js-bulk-stock');
    stockInputs.forEach(input => {
        if (input.value === "0") input.value = "";
    });

    const priceInputs = document.querySelectorAll('.js-input-price, .js-bulk-price');
    priceInputs.forEach(input => {
        let val = String(input.value).trim();
        if (val === "0" || val === "0.00" || val === "0,00") {
            input.value = "";
        }
        if (input.value) {
            val = String(input.value);
            if (val.includes('.') && !val.includes(',')) {
                val = val.replace('.', ',');
            }
            input.value = val;
            handlePriceBlur({ target: input });
        }
        input.addEventListener('input', formatPriceField);
        input.addEventListener('blur', handlePriceBlur);
    });

    /* ----------------------------------------------------------
     * 4. Toplu İşlem (Bulk Apply)
     * ---------------------------------------------------------- */
    const bulkPriceInput = document.querySelector(".js-bulk-price");
    const bulkStockInput = document.querySelector(".js-bulk-stock");
    const btnBulkApply = document.querySelector(".js-btn-bulk-apply");

    btnBulkApply?.addEventListener("click", () => {
        if (bulkPriceInput.value) {
            handlePriceBlur({ target: bulkPriceInput });
        }

        const priceVal = bulkPriceInput.value.trim();
        const stockVal = bulkStockInput.value.trim();
        const skuVal = bulkSkuInput.value.trim();
        const mode = document.querySelector('input[name="bulk_mode"]:checked').value; 

        let appliedCount = 0;
        const rows = document.querySelectorAll(".js-offer-row");

        rows.forEach((row, index) => {
            const pInput = row.querySelector(".js-input-price");
            const sInput = row.querySelector(".js-input-stock");
            const kInput = row.querySelector(".js-input-sku");

            if (priceVal && (mode === "all" || !pInput.value)) {
                pInput.value = priceVal;
                appliedCount++;
            }

            if (stockVal && (mode === "all" || !sInput.value)) {
                sInput.value = stockVal;
                appliedCount++;
            }

            if (skuVal && (mode === "all" || !kInput.value)) {
                let finalSku = skuVal;

                if (isMagicPrefix) {
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
     * 5. Ana Form Kaydetme
     * ---------------------------------------------------------- */
    const offerForm = document.getElementById("offer-form");
    const btnSubmitOffer = document.getElementById("btn-submit-offer");
    const hiddenVariantsData = document.getElementById("variants_data_input");

    btnSubmitOffer?.addEventListener("click", async (e) => {
        e.preventDefault();
        
        // Hataları temizle
        document.querySelectorAll(".is-invalid").forEach(el => el.classList.remove("is-invalid"));

        const rows = document.querySelectorAll(".js-offer-row");
        const variantsData = [];
        const seenSkus = new Set();
        const seenBarcodes = new Set();
        let hasError = false;

        rows.forEach(row => {
            const id = row.dataset.id;
            const type = row.dataset.type; // "existing" veya "custom"
            
            const pInput = row.querySelector(".js-input-price");
            const sInput = row.querySelector(".js-input-stock");
            const kInput = row.querySelector(".js-input-sku");
            const bInput = row.querySelector(".js-input-barcode");

            // Formatlı UI fiyatını backend fiyatına (Örn: 1.000,50 -> 1000.50) çevir
            const cleanPrice = pInput.value.replace(/\./g, '').replace(',', '.'); 
            const stock = sInput.value.trim();
            const sku = kInput ? kInput.value.trim() : "";
            const barcode = bInput ? bInput.value.trim() : "";

            // Eğer Fiyat VEYA Stok BOŞ ise, bu satırı Yoksay (Skip)
            if (!cleanPrice && !stock) {
                return; // Satıcı bunu satmak istemiyor
            }

            // Eğer biri dolu biri boşsa hata ver
            if ((cleanPrice && !stock) || (!cleanPrice && stock)) {
                WizardUI.showToast("error", "Lütfen teklif verdiğiniz varyantların hem Fiyat hem de Stok alanlarını doldurun.");
                pInput.classList.add("is-invalid");
                sInput.classList.add("is-invalid");
                hasError = true;
                return;
            }

            // Temel doğrulama / Backend'de de yapılacak
            if (parseFloat(cleanPrice) <= 0) {
                pInput.classList.add("is-invalid");
                hasError = true;
            }

            if (sku) {
                const lowerSku = sku.toLowerCase();
                if (seenSkus.has(lowerSku)) {
                    kInput.classList.add("is-invalid");
                    WizardUI.showToast("error", "Tabloda aynı SKU birden fazla kez kullanılamaz.");
                    hasError = true;
                }
                seenSkus.add(lowerSku);
            }

            if (barcode) {
                const lowerBarcode = barcode.toLowerCase();
                if (seenBarcodes.has(lowerBarcode)) {
                    // Çakışma var. Sadece satıcının değiştirebildiği (yeni eklenen) inputu kırmızı yap
                    if (bInput && !bInput.disabled) {
                        bInput.classList.add("is-invalid");
                    }
                    WizardUI.showToast("error", `"${barcode}" barkodu tabloda tekrar ediyor veya katalogda zaten mevcut.`);
                    hasError = true;
                }
                seenBarcodes.add(lowerBarcode);
            }

            // JSON formatına uygun objeyi ekle
            variantsData.push({
                type: type,
                id: id,
                price: cleanPrice,
                stock: stock,
                sku: sku || null,
                barcode: barcode || null
            });
        });

        if (hasError) return;

        if (variantsData.length === 0) {
            WizardUI.showToast("warning", "Satışa başlamak için en az bir varyanta fiyat ve stok girmelisiniz.");
            return;
        }

        // Veriyi string yap ve gizli input'a bas
        hiddenVariantsData.value = JSON.stringify(variantsData);

        // Formu Gönder (AJAX ile)
        WizardUI.setButtonLoading(btnSubmitOffer, true, "Kaydediliyor...");

        try {
            const formData = new FormData(offerForm);
            const response = await fetch(offerForm.action, {
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
                WizardUI.showToast("error", data.message || "Kaydetme sırasında bir hata oluştu.");
                if (data.error_barcode) {
                    const errorBarcodeLower = data.error_barcode.toLowerCase();
                    const allRows = document.querySelectorAll(".js-offer-row");
                    
                    allRows.forEach(row => {
                        const bInput = row.querySelector(".js-input-barcode");
                        // Gelen hatadaki barkod ile eşleşen inputu bul
                        if (bInput && bInput.value.trim().toLowerCase() === errorBarcodeLower) {
                            bInput.classList.add("is-invalid"); // Textbox Kırmızı olur
                            
                            // Kullanıcının görebilmesi için ekranı o satıra kaydır
                            bInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        }
                    });
                }
                // --------------------------------------------------------

                WizardUI.setButtonLoading(btnSubmitOffer, false);
                return;
            }

            // Başarılı!
            await Swal.fire({
                title: 'Tebrikler!',
                text: data.message || 'Teklifleriniz başarıyla yayına alındı.',
                icon: 'success',
                confirmButtonColor: 'var(--teal-600)',
                confirmButtonText: 'Ürünlerime Git'
            });

            if (data.redirect_url) {
                window.location.href = data.redirect_url;
            }

        } catch (error) {
            console.error(error);
            WizardUI.showToast("error", "Sunucuya ulaşılamadı.");
            WizardUI.setButtonLoading(btnSubmitOffer, false);
        }
    });

    /* ----------------------------------------------------------
     * 6. Yeni Varyant Modalı (Custom Variant Contribution)
     * ---------------------------------------------------------- */
    const modal = document.getElementById("custom-variant-modal");
    const btnOpenModal = document.getElementById("btn-open-custom-modal");
    const btnCloseModal = document.getElementById("btn-close-modal");
    const btnCancelModal = document.getElementById("btn-cancel-modal");
    const customVariantForm = document.getElementById("custom-variant-form");
    const hiddenAttributesData = document.getElementById("modal_attributes_data");
    const btnSaveModal = document.getElementById("btn-save-modal");
    const dynamicAttrsContainer = document.getElementById("modal-dynamic-attributes");

    // GÖRSELLER İÇİN SANAL DİZİ (VIRTUAL ARRAY)
    let modalFiles = [];
    let isAttributesLoaded = false;

    async function loadCategoryAttributes() {
        if (isAttributesLoaded) return; // Zaten yüklüyse API'ye vurma
        
        const url = modal.dataset.attributesUrl;
        if (!url) return;

        try {
            const response = await fetch(url);
            const data = await response.json();

            if (!response.ok) throw new Error("Özellikler çekilemedi.");

            dynamicAttrsContainer.innerHTML = ""; // Yükleniyor yazısını sil

            // Backend'den beklenen JSON: [{ id: 1, name: "Renk", allow_custom: true, values: [{id:15, val:"Siyah"}] }]
            data.results.forEach(attr => {
                const groupDiv = document.createElement('div');
                groupDiv.className = 'pw-form-group';
                
                // Select Box Oluştur
                let html = `
                    <label class="pw-form-label">${attr.name} <span class="pw-required">*</span></label>
                    <select class="pw-form-control js-modal-attr-select" data-attr-id="${attr.id}" data-allow-custom="${attr.allow_custom_values}">
                        <option value="">Seçiniz...</option>
                        ${attr.values.map(v => `<option value="${v.id}">${v.value}</option>`).join('')}
                        ${attr.allow_custom_values ? `<option value="CUSTOM">-- Yeni Değer Gir --</option>` : ''}
                    </select>
                `;

                // Eğer "Yeni Değer Gir" izni varsa Gizli Input ekle
                if (attr.allow_custom_values) {
                    html += `
                        <input type="text" class="pw-form-control js-modal-attr-custom" placeholder="Örn: Titanyum" style="display:none; margin-top: 8px;">
                    `;
                }

                groupDiv.innerHTML = html;
                dynamicAttrsContainer.appendChild(groupDiv);

                // Event Listener: Select değiştiğinde Custom Input'u göster/gizle
                const selectEl = groupDiv.querySelector('.js-modal-attr-select');
                const customEl = groupDiv.querySelector('.js-modal-attr-custom');
                
                selectEl.addEventListener('change', (e) => {
                    // EĞER BU ÖZELLİK İÇİN CUSTOM İNPUT VARSA İŞLEM YAP
                    if (customEl) { 
                        if (e.target.value === "CUSTOM") {
                            customEl.style.display = "block";
                            customEl.required = true;
                        } else {
                            customEl.style.display = "none";
                            customEl.required = false;
                            customEl.value = "";
                        }
                    }
                    validateModalForm();
                });

                // Özel değer yazarken klavye her hareket ettiğinde kontrol et
                if (customEl) {
                    customEl.addEventListener('input', () => {
                        validateModalForm();
                    });
                }
            });

            isAttributesLoaded = true;
            validateModalForm(); // Yükleme biter bitmez butonu kontrol et ve kilitle

        } catch (err) {
            console.error(err);
            dynamicAttrsContainer.innerHTML = `<div style="color:var(--red);">Özellikler yüklenemedi. Sayfayı yenileyin.</div>`;
        }
    }

    // Modal içindeki zorunlu alanlar dolduruldu mu?
    function validateModalForm() {
        let isValid = true;
        let isCustomSelected = false;
        let currentSignatureIds = [];


        const selects = dynamicAttrsContainer.querySelectorAll('.js-modal-attr-select');
        const warningBox = document.getElementById("modal-duplicate-warning");
        
        selects.forEach(select => {
            if (select.value === "") {
                isValid = false;
            } else if (select.value === "CUSTOM") {
                isCustomSelected = true;
                const customInput = select.parentElement.querySelector('.js-modal-attr-custom');
                if (!customInput || customInput.value.trim() === "") isValid = false;
            } else {
                // Seçilen ID'leri listeye at
                currentSignatureIds.push(select.value);
            }
        });

        // ---------------------------------------------
        // DUPLICATE (MÜKERRER) KONTROLÜ
        // ---------------------------------------------
        // Eğer temel alanlar doluysa ve kullanıcı "YENİ DEĞER GİR (CUSTOM)" SEÇMEDİYSE:
        if (isValid && !isCustomSelected) {
            // ID'leri küçükten büyüğe sırala ve birleştir
            currentSignatureIds.sort((a, b) => parseInt(a) - parseInt(b));
            const currentSig = currentSignatureIds.join(',');

            // Eğer bu kombinasyon tabloda zaten varsa:
            if (existingSignatures.has(currentSig)) {
                isValid = false; // Formu geçersiz kıl
                if (warningBox) warningBox.style.display = "block"; // Uyarıyı göster
            } else {
                if (warningBox) warningBox.style.display = "none";
            }
        } else {
            if (warningBox) warningBox.style.display = "none";
        }

        // Butonu sadece her şey geçerliyse aktif et
        btnSaveModal.disabled = !isValid;
        return isValid;
    }

    // Modalı aç
    btnOpenModal?.addEventListener("click", () => {
        modal.hidden = false;
        loadCategoryAttributes(); // Sadece ilk açılışta fetch eder
    });

    function closeModal() {
        modal.hidden = true;
        customVariantForm.reset();
        // Selectleri eski haline getir (Custom alanları gizle)
        dynamicAttrsContainer.querySelectorAll('.js-modal-attr-custom').forEach(el => el.style.display = "none");

        if (offerUploadPreview) offerUploadPreview.innerHTML = "";

        // Modal kapanırken resim hafızasını ve UI'ı temizle
        modalFiles = [];
        renderModalImages();
        
        validateModalForm();
    }

    btnCloseModal?.addEventListener("click", closeModal);
    btnCancelModal?.addEventListener("click", closeModal);

    const existingSignatures = new Set();
    document.querySelectorAll('.js-offer-row').forEach(row => {
        const sigStr = row.getAttribute('data-signature'); // Django'dan (15, 22) gibi gelir
        if (sigStr) {
            const ids = sigStr.match(/\d+/g); // İçindeki sayıları kopartır
            if (ids) {
                // Sayıları küçükten büyüğe sıralayıp string olarak kaydediyoruz
                existingSignatures.add(ids.sort((a,b) => parseInt(a) - parseInt(b)).join(','));
            }
        }
    });

    // --- GÖRSEL YÖNETİMİ (CLIENT-SIDE) ---
    const offerUploadZone = document.getElementById("offer-upload-zone");
    const offerImageInput = document.getElementById("offer-image-input");
    const offerUploadPreview = document.getElementById("offer-upload-preview");

    function generateId() { return Math.random().toString(36).substr(2, 9); }

    function renderModalImages() {
        offerUploadPreview.innerHTML = "";
        if (modalFiles.length === 0) return;

        const grid = document.createElement("div");
        grid.className = "pw-image-grid"; // Step 3'teki CSS sınıfı
        
        modalFiles.forEach((mFile) => {
            const card = document.createElement("div");
            card.className = "pw-image-card";
            card.dataset.id = mFile.id;
            
            card.innerHTML = `
                <div class="pw-image-preview">
                    ${mFile.isMain ? `<span class="pw-image-main-badge"><i class="fa-solid fa-star"></i> Kapak</span>` : ''}
                    <img src="${mFile.url}" alt="Önizleme" draggable="false">
                </div>
                <div class="pw-image-actions">
                    <div class="pw-image-actions-group">
                        <!-- Yazı gizlendi, Tooltip eklendi -->
                        <button type="button" class="pw-btn-text js-modal-make-main ${mFile.isMain ? 'is-main' : ''}" title="Kapak Yap">
                            <i class="fa-solid fa-star"></i> <span style="font-size: 0.7rem;">Kapak</span>
                        </button>
                        <button type="button" class="pw-btn-text js-modal-edit-alt ${mFile.altText ? 'has-alt' : ''}" title="${mFile.altText || 'Alt Metin Ekle'}">
                            <i class="fa-solid fa-pen"></i>
                        </button>
                    </div>
                    <button type="button" class="pw-image-delete js-modal-delete-image" title="Görseli Sil"><i class="fa-solid fa-trash-can"></i></button>
                </div>
            `;
            
            // Silme Olayı
            card.querySelector('.js-modal-delete-image').addEventListener("click", () => {
                modalFiles = modalFiles.filter(f => f.id !== mFile.id);
                if (mFile.isMain && modalFiles.length > 0) modalFiles[0].isMain = true; // Kapağı sildiyse ilkini kapak yap
                renderModalImages();
            });
            
            // Kapak Yapma Olayı
            card.querySelector('.js-modal-make-main').addEventListener("click", () => {
                modalFiles.forEach(f => f.isMain = false);
                mFile.isMain = true;
                renderModalImages();
            });

            // Alt Metin Ekleme Olayı (SweetAlert ile)
            card.querySelector('.js-modal-edit-alt').addEventListener("click", async () => {
                const { value: text, isConfirmed } = await Swal.fire({
                    title: 'Alt Metin (SEO) Ekle',
                    input: 'text',
                    inputValue: mFile.altText,
                    showCancelButton: true,
                    confirmButtonText: 'Kaydet',
                    cancelButtonText: 'İptal',
                    confirmButtonColor: 'var(--teal-600)'
                });
                if (isConfirmed) {
                    mFile.altText = text;
                    renderModalImages(); // Kalem ikonunu renklendirmek için UI'ı yenile
                }
            });
            
            grid.appendChild(card);
        });
        
        offerUploadPreview.appendChild(grid);
        
        // Step 3'teki gibi Sortable.js (Sıralama) Aktifleştiriliyor
        if (typeof Sortable !== "undefined") {
            new Sortable(grid, {
                animation: 150,
                ghostClass: 'sortable-ghost',
                delay: 100,                // Mobilde yanlış tıklamayı önler
                delayOnTouchOnly: true,    // Gecikmeyi sadece mobilde uygular (Masaüstünde anında sürüklenir)
                onEnd: function (evt) {
                    // DOM'daki yeni sırayı alıp sanal dizimizi güncelliyoruz
                    const newOrderIds = Array.from(grid.querySelectorAll('.pw-image-card')).map(el => el.dataset.id);
                    const reorderedFiles = [];
                    newOrderIds.forEach(id => {
                        const f = modalFiles.find(x => x.id === id);
                        if(f) reorderedFiles.push(f);
                    });
                    modalFiles = reorderedFiles;
                }
            });
        } else {
            console.error("HATA: Sortable.js kütüphanesi yüklenmemiş!");
            WizardUI.showToast("error", "Sürükle-bırak sıralaması için Sortable.js eksik. Lütfen offer.html'e ekleyin.");
        }
    }

    if (offerUploadZone && offerImageInput) {
        offerUploadZone.addEventListener("click", () => offerImageInput.click());

        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            offerUploadZone.addEventListener(eventName, (e) => {
                e.preventDefault(); e.stopPropagation();
            });
        });

        ['dragenter', 'dragover'].forEach(eventName => {
            offerUploadZone.addEventListener(eventName, () => {
                offerUploadZone.style.borderColor = "var(--teal-500)";
                offerUploadZone.style.backgroundColor = "var(--teal-50)";
            });
        });

        ['dragleave', 'drop'].forEach(eventName => {
            offerUploadZone.addEventListener(eventName, () => {
                offerUploadZone.style.borderColor = "var(--gray-300)";
                offerUploadZone.style.backgroundColor = "var(--gray-50)";
            });
        });

        offerUploadZone.addEventListener('drop', (e) => {
            if (e.dataTransfer.files.length) handleFileSelection(e.dataTransfer.files);
        });

        offerImageInput.addEventListener('change', (e) => {
            handleFileSelection(e.target.files);
        });

        function handleFileSelection(files) {
            const newFiles = Array.from(files);
            if (modalFiles.length + newFiles.length > 5) {
                WizardUI.showToast("warning", "En fazla 5 görsel seçebilirsiniz.");
                return;
            }
            newFiles.forEach(file => {
                modalFiles.push({
                    id: generateId(),
                    file: file,
                    url: URL.createObjectURL(file), // Tarayıcıda anlık önizleme için sahte URL üretir
                    isMain: modalFiles.length === 0, // Eğer ilk resimse otomatik kapak yap
                    altText: ""
                });
            });
            offerImageInput.value = ""; // Aynı dosyayı tekrar seçebilmek için inputu sıfırla
            renderModalImages();
        }
    }

    // Custom Form Submit (Veriyi Toplayıp JSON Yapma)
    customVariantForm?.addEventListener("submit", async (e) => {
        e.preventDefault();

        if (!validateModalForm()) return;

        // 1. DOM'dan seçilen özellikleri JSON Array'e çevir
        const finalAttributes = [];
        const selects = dynamicAttrsContainer.querySelectorAll('.js-modal-attr-select');
        
        selects.forEach(select => {
            const attrId = select.dataset.attrId;
            let obj = { attribute_id: attrId };
            
            if (select.value === "CUSTOM") {
                const customVal = select.parentElement.querySelector('.js-modal-attr-custom').value.trim();
                obj["custom_val"] = customVal;
            } else {
                obj["value_id"] = select.value;
            }
            finalAttributes.push(obj);
        });
        
        hiddenAttributesData.value = JSON.stringify(finalAttributes);

        WizardUI.setButtonLoading(btnSaveModal, true, "Ekleniyor...");

        try {
            // 2. FormData'yı hazırla
            const formData = new FormData(customVariantForm);

            // Gizli inputta arta kalan default dosyaları temizle (Sanal array'dekileri göndereceğiz)
            formData.delete("images");

            // 3. Backend (is_main = index == 0) mantığını kullandığı için;
            // Kapak yapılan resmi dizinin en başına (0. indexe) taşıyoruz!
            const mainIndex = modalFiles.findIndex(f => f.isMain);
            if (mainIndex > 0) {
                const mainFile = modalFiles.splice(mainIndex, 1)[0];
                modalFiles.unshift(mainFile); 
            }

            // 4. Sıralanmış ve Kapağı başa alınmış resimleri formData'ya gerçek dosya olarak ekle
            modalFiles.forEach(mFile => {
                formData.append("images", mFile.file);
            });
            
            const response = await fetch(customVariantForm.action, {
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
                if (data.errors) {
                    const errorMsgs = Object.values(data.errors).map(e => e[0].message).join("\n");
                    WizardUI.showToast("error", errorMsgs);
                } else {
                    WizardUI.showToast("error", data.message || "Varyant eklenemedi.");
                }
                WizardUI.setButtonLoading(btnSaveModal, false);
                return;
            }

            // BAŞARILI! Sayfayı yeniliyoruz ki tabloya eklensin
            WizardUI.showToast("success", "Yeni seçenek başarıyla eklendi.");
            closeModal();
            setTimeout(() => { window.location.reload(); }, 1000);

        } catch (error) {
            console.error(error);
            WizardUI.showToast("error", "Sunucu ile bağlantı kurulamadı.");
            WizardUI.setButtonLoading(btnSaveModal, false);
        }
    });

    /* ----------------------------------------------------------
     * 7. Eklenen Varyantı Silme İşlemi
     * ---------------------------------------------------------- */
    const deleteButtons = document.querySelectorAll(".js-btn-delete");
    
    deleteButtons.forEach(btn => {
        btn.addEventListener("click", async (e) => {
            const url = e.currentTarget.dataset.url;
            
            // SweetAlert ile onay iste
            const confirm = await Swal.fire({
                title: 'Emin misiniz?',
                text: 'Eklediğiniz bu özel seçenek taslaktan kalıcı olarak silinecek.',
                icon: 'warning',
                showCancelButton: true,
                confirmButtonColor: '#dc2626', // Kırmızı
                cancelButtonColor: '#6b7280',
                confirmButtonText: 'Evet, Sil',
                cancelButtonText: 'İptal'
            });

            if(confirm.isConfirmed) {
                try {
                    // Backend'deki VariantDeleteView'e istek atıyoruz 
                    const response = await fetch(url, {
                        method: "POST", 
                        headers: {
                            "X-CSRFToken": csrftoken,
                            "X-Requested-With": "XMLHttpRequest",
                        }
                    });
                    
                    if (response.ok) {
                        WizardUI.showToast("success", "Varyant başarıyla silindi.");
                        // Tablonun güncellenmesi için sayfayı yeniliyoruz
                        setTimeout(() => window.location.reload(), 800);
                    } else {
                        WizardUI.showToast("error", "Silme işlemi başarısız oldu.");
                    }
                } catch(err) {
                    WizardUI.showToast("error", "Sunucuya ulaşılamadı.");
                }
            }
        });
    });

    
});