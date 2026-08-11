/* ==========================================================
 * STEP 3 - Image Management
 * ========================================================== */

/* ----------------------------------------------------------
 * Elements & State
 * ---------------------------------------------------------- */
const groupForm = document.getElementById("image-group-create-form");
const groupsContainer = document.getElementById("image-groups-container");
const completeForm = document.getElementById("step3-complete-form");
const completeBtn = document.querySelector(".js-complete-step3");

let sortableInstances = [];

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
 * Core Helpers
 * ---------------------------------------------------------- */
function refreshGroupsHTML(html, count) {
    if (!groupsContainer) return;
    
    // HTML'i Yenile
    groupsContainer.innerHTML = html;
    
    // Sayaçları Güncelle
    const countEl = document.querySelector('.js-total-groups-count');
    if (countEl) countEl.textContent = count;

    // Her AJAX yenilemesinden sonra Sürükle&Bırak sıralamasını tekrar aktifleştir
    initSortable();
    initChoices();
}

async function sendAjaxRequest(url, formData, loadingText = "İşleniyor...") {
    const loadingToast = WizardUI.showLoading(loadingText);

    try {
        const response = await fetch(url, {
            method: "POST",
            headers: {
                "X-CSRFToken": csrftoken,
                "X-Requested-With": "XMLHttpRequest",
            },
            body: formData,
        });

        let data = {};
        try { data = await response.json(); } catch {}

        if (!response.ok || !data.success) {
            if (data.errors) {
                const messages = Object.values(data.errors).flat();
                WizardUI.showToast("error", messages.join("\n"));
            } else {
                WizardUI.showToast("error", data.message || "Bir hata oluştu.");
            }
            return false;
        }

        if (data.html) {
            refreshGroupsHTML(data.html, data.count);
        }

        WizardUI.showToast("success", data.message);
        return data;

    } catch (error) {
        console.error(error);
        WizardUI.showToast("error", "Sunucuya ulaşılamadı.");
        return false;
    } finally {
        if (loadingToast) loadingToast.close();
    }
}

/* ----------------------------------------------------------
 * Sortable.js (Görsel Sıralama)
 * ---------------------------------------------------------- */
function initSortable() {
    // Eski instanceları temizle (Memory leak olmaması için)
    sortableInstances.forEach(inst => inst.destroy());
    sortableInstances = [];

    document.querySelectorAll('.js-sortable-grid').forEach(grid => {
        const url = grid.dataset.url;
        if (!url) return;

        const inst = new Sortable(grid, {
            animation: 150,
            ghostClass: 'sortable-ghost',
            delay: 100, // Mobilde yanlışlıkla sürüklemeyi önlemek için
            delayOnTouchOnly: true,
            onEnd: function (evt) {
                // Sıralama bittiğinde yeni id sırasını al
                const imageIds = Array.from(grid.querySelectorAll('.js-image-item')).map(el => el.dataset.id);
                saveReorder(url, imageIds, grid);
            }
        });
        sortableInstances.push(inst);
    });
}

function initChoices() {
    if (typeof Choices === "undefined") return;

    document.querySelectorAll(".pw-card select").forEach(select => {
        // Zaten Choices'a çevrilmişse tekrar işlem yapma
        if (select.closest('.choices') || select.choicesInstance) return;

        try {
            const optionCount = [...select.options]
                .filter(opt => opt.value.trim() !== "")
                .length;

            const instance = new Choices(select, {
                searchEnabled: optionCount > 5,
                searchPlaceholderValue: 'Ara...',
                itemSelectText: '', // Sağdaki "Press to select" yazısını kaldırır
                noResultsText: 'Sonuç bulunamadı',
                noChoicesText: 'Seçenek yok',
                shouldSort: false, // Django'nun verdiği sıralamayı bozmaması için
                position: 'bottom', // Menünün her zaman aşağı doğru açılmasını zorlar
            });
            
            select.choicesInstance = instance;
        } catch (e) {
            console.error("Choices.js başlatılamadı:", e);
        }
    });
}

async function saveReorder(url, imageIds, grid) {
    const formData = new FormData();
    imageIds.forEach(id => formData.append('image_ids[]', id));
    
    grid.style.opacity = '0.5';
    await sendAjaxRequest(url, formData, "Sıralama kaydediliyor...");
    grid.style.opacity = '1';
}

/* ----------------------------------------------------------
 * 1. Grup Oluşturma
 * ---------------------------------------------------------- */
groupForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = groupForm.querySelector('button[type="submit"]');

    WizardUI.setButtonLoading(btn, true, "Ekleniyor...");

    const formData = new FormData(groupForm);
    const success = await sendAjaxRequest(groupForm.action, formData, "Grup oluşturuluyor...");

    if (success) {
        // Formdaki dropdownları sıfırla (Ortak seçeneğine döndür)
        groupForm.querySelectorAll("select").forEach(sel => {
            sel.selectedIndex = 0;
            // Eğer Choices.js kullanıyorsak:
            if (sel.choicesInstance) sel.choicesInstance.setChoiceByValue('');
        });
    }

    WizardUI.setButtonLoading(btn, false);

    if (success) {
        groupForm.dispatchEvent(new Event('change'));
    }
});

function initGroupFormValidation() {
    if (!groupForm) return;
    
    const submitBtn = groupForm.querySelector('button[type="submit"]');
    if (!submitBtn) return;

    const selects = Array.from(groupForm.querySelectorAll("select"));

    // Buton durumunu güncelleyen fonksiyon
    const toggleButtonState = () => {
        // Sadece "Seçiniz" (value="") opsiyonu barındıran (birden çok seçenekli/değişken) dropdown'ları bul
        const variableSelects = selects.filter(s => Array.from(s.options).some(opt => opt.value === ""));

        // En az bir select içinde boş olmayan bir değer seçilmiş mi?
        // const hasSelection = selects.some(select => select.value.trim() !== "");

        let hasSelection = false;

        if (variableSelects.length > 0) {
            // Eğer değişken özellikler varsa, onlardan en az biri seçilmiş olmalı
            hasSelection = variableSelects.some(select => select.value.trim() !== "");
        } else {
            // Eğer hiç değişken özellik yoksa (hepsi tek seçenekli ve mecburen seçiliyse), form geçerlidir
            hasSelection = true;
        }
        
        // Seçim yoksa butonu disable yap, varsa enable yap
        submitBtn.disabled = !hasSelection;
        
        // Görsel olarak da soluk görünmesi için (isteğe bağlı, CSS'ine göre ayarlayabilirsin)
        if (!hasSelection) {
            submitBtn.style.opacity = "0.5";
            submitBtn.style.cursor = "not-allowed";
        } else {
            submitBtn.style.opacity = "1";
            submitBtn.style.cursor = "pointer";
        }
    };

    // Sayfa ilk yüklendiğinde kontrol et
    toggleButtonState();

    // Select değerleri değiştikçe kontrol et
    // (Choices.js change eventini tetikler, bu yüzden normal change dinlemek yeterlidir)
    groupForm.addEventListener("change", toggleButtonState);
}

/* ----------------------------------------------------------
 * 2. Event Delegation (Tıklama İşlemleri)
 * AJAX ile yenilenen elemanlar için kapsayıcı üzerinden yakalıyoruz.
 * ---------------------------------------------------------- */
groupsContainer?.addEventListener("click", async (e) => {
    
    // A. Dropzone'a tıklanınca Dosya Seçiciyi Aç
    const dropzone = e.target.closest('.js-upload-zone');
    if (dropzone) {
        const input = dropzone.querySelector('input[type="file"]');
        if (input && e.target !== input) {
            input.click();
        }
        return;
    }

    const defaultGroupBtn = e.target.closest('.js-create-default-group');
    if (defaultGroupBtn) {
        WizardUI.setButtonLoading(defaultGroupBtn, true, "Oluşturuluyor...");
        const formData = new FormData();
        await sendAjaxRequest(defaultGroupBtn.dataset.url, formData, "Ortak yükleme alanı oluşturuluyor...");
        return;
    }

    // B. Grubu Sil
    const deleteGroupBtn = e.target.closest('.js-delete-group');
    if (deleteGroupBtn) {
        const confirmed = await WizardUI.showConfirm({
            title: "Görsel Grubu Silinecek",
            message: "Bu gruba ait tüm görseller de silinecektir. Emin misiniz?",
            confirmText: "Grubu Sil"
        });
        if (confirmed) {
            const formData = new FormData(); // Boş
            await sendAjaxRequest(deleteGroupBtn.dataset.url, formData, "Grup siliniyor...");
        }
        return;
    }

    // C. Görseli Sil
    const deleteImageBtn = e.target.closest('.js-delete-image');
    if (deleteImageBtn) {
        const confirmed = await WizardUI.showConfirm({
            title: "Görsel Silinecek",
            message: "Görseli kalıcı olarak silmek istiyor musunuz?",
            confirmText: "Sil"
        });
        if (confirmed) {
            const formData = new FormData();
            await sendAjaxRequest(deleteImageBtn.dataset.url, formData, "Görsel siliniyor...");
        }
        return;
    }

    // D. Kapak Fotoğrafı Yap
    const mainBtn = e.target.closest('.js-make-main');
    if (mainBtn) {
        // Zaten kapaksa hiçbir şey yapma
        if (mainBtn.classList.contains('is-main')) return;
        
        const formData = new FormData();
        formData.append("is_main", "True");
        await sendAjaxRequest(mainBtn.dataset.url, formData, "Kapak güncelleniyor...");
        return;
    }

    // E. Alt Metin Ekle/Düzenle (SweetAlert2 Popup ile)
    const altBtn = e.target.closest('.js-edit-alt');
    if (altBtn) {
        const currentAlt = altBtn.dataset.currentAlt || "";
        
        const { value: text, isConfirmed } = await Swal.fire({
            title: 'Alt Metin (SEO) Ekle',
            input: 'text',
            inputLabel: 'Görselin ne olduğunu (Örn: Siyah deri ceket) açıklayın.',
            inputValue: currentAlt,
            showCancelButton: true,
            confirmButtonText: 'Kaydet',
            cancelButtonText: 'İptal',
            confirmButtonColor: 'var(--teal-600)',
            inputAttributes: {
                maxlength: 200,
                autocapitalize: 'off',
                autocorrect: 'off'
            }
        });

        if (isConfirmed) { // İptal edilmediyse
            const formData = new FormData();
            formData.append("alt_text", text);
            await sendAjaxRequest(altBtn.dataset.url, formData, "Alt metin kaydediliyor...");
        }
        return;
    }
});

/* ----------------------------------------------------------
 * 3. Görsel Yükleme İşlemi (Upload)
 * ---------------------------------------------------------- */
// Input'tan dosya seçilince tetiklenen olay
groupsContainer?.addEventListener('change', (e) => {
    if (e.target.matches('input[type="file"]')) {
        handleFileUpload(e.target, e.target.files);
    }
});

// Yükleme fonksiyonu
async function handleFileUpload(inputElement, files) {
    if (!files || files.length === 0) return;

    const MAX_SIZE_MB = 10;
    const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024;

    const oversizedFile = Array.from(files).find(file => file.size > MAX_SIZE_BYTES);
    if (oversizedFile) {
        WizardUI.showToast("error", `Dosya boyutu çok büyük: ${oversizedFile.name}. Maksimum boyut ${MAX_SIZE_MB}MB olmalıdır.`);
        inputElement.value = ""; // Seçimi sıfırla
        return;
    }

    const form = inputElement.closest('.js-upload-form');
    const dropzone = form.closest('.js-upload-zone');
    const url = form.action;

    if (dropzone && dropzone.dataset.uploading === "1") {
        WizardUI.showToast(
            "warning",
            "Bu grup için yükleme devam ediyor."
        );
        return;
    }

    dropzone.dataset.uploading = "1";

    const formData = new FormData();
    // Input isminin "images" olmasına dikkat
    Array.from(files).forEach(file => {
        formData.append("images", file);
    });

    // UI'ı Loading konumuna al
    if (dropzone) {
        dropzone.style.opacity = '0.6';
        dropzone.style.pointerEvents = 'none';
    }

    try {
        await sendAjaxRequest(
            url,
            formData,
            "Görseller yükleniyor..."
        );
    } finally {
        delete dropzone.dataset.uploading;

        inputElement.value = "";

        if (dropzone) {
            dropzone.style.opacity = "1";
            dropzone.style.pointerEvents = "all";
        }
    }
}


/* ----------------------------------------------------------
 * 4. Sürükle ve Bırak (Drag & Drop) Efektleri
 * ---------------------------------------------------------- */
if (groupsContainer) {
    // Tarayıcının varsayılan olarak resmi sekmede açmasını engelle
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        groupsContainer.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults (e) {
        e.preventDefault();
        e.stopPropagation();
    }

    groupsContainer.addEventListener('dragenter', (e) => {
        const dz = e.target.closest('.js-upload-zone');
        if (dz) dz.classList.add('dragover');
    });

    groupsContainer.addEventListener('dragleave', (e) => {
        const dz = e.target.closest('.js-upload-zone');
        if (dz) dz.classList.remove('dragover');
    });

    groupsContainer.addEventListener('drop', (e) => {
        const dz = e.target.closest('.js-upload-zone');
        if (dz) {
            dz.classList.remove('dragover');
            const files = e.dataTransfer.files;
            const input = dz.querySelector('input[type="file"]');
            
            if (input && files.length) {
                // Seçilen dosyaları yükle fonksiyonuna gönder
                handleFileUpload(input, files);
            }
        }
    });
}


/* ----------------------------------------------------------
 * 5. Adımı Tamamla (İleri Butonu)
 * ---------------------------------------------------------- */
completeForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!completeBtn) return;

    WizardUI.setButtonLoading(completeBtn, true, "Devam Ediliyor...");

    const formData = new FormData(completeForm);

    try {
        const response = await fetch(completeForm.action, {
            method: "POST",
            headers: {
                "X-CSRFToken": csrftoken,
                "X-Requested-With": "XMLHttpRequest",
            },
            body: formData,
        });

        let data = {};
        try { data = await response.json(); } catch {}

        if (!response.ok || !data.success) {
            WizardUI.showToast("error", data.message || "Lütfen gerekli görselleri yüklediğinizden emin olun.");
            WizardUI.setButtonLoading(completeBtn, false);
            return;
        }

        if (data.redirect_url) {
            window.location.href = data.redirect_url;
        }

    } catch (error) {
        console.error(error);
        WizardUI.showToast("error", "Sunucuya ulaşılamadı.");
        WizardUI.setButtonLoading(completeBtn, false);
    }
});


/* ----------------------------------------------------------
 * Init
 * ---------------------------------------------------------- */
document.addEventListener("DOMContentLoaded", function () {

    // 1. ÖNCE HAYATİ FONKSİYONU BAŞLAT (Böylece başka bir hata bunu engellemez)
    initSortable();

    // 2. Kütüphane yüklenmiş mi diye kontrol ederek Select'leri şıklaştır
    initChoices();

    // 3. "Seçiniz" konumundaysa butona tıklanamaz
    initGroupFormValidation();
});

// 3. Tarayıcının "Geri" tuşuyla (BFCache - Hafızadan) gelinirse 
// DOMContentLoaded tekrar tetiklenmez, bu yüzden pageshow kullanıyoruz.
// (Bunu DOMContentLoaded'ın dışına aldık ki scope sorunu olmasın)
window.addEventListener('pageshow', function (event) {
    if (event.persisted) {
        // Eski instanceları güvenli bir şekilde silip yeniden kur
        if (sortableInstances) {
            sortableInstances.forEach(inst => {
                try { inst.destroy(); } catch(e){}
            });
        }
        initSortable();
    }
});