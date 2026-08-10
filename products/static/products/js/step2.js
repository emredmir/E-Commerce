/* ==========================================================
 * STEP 2 - Variant Management
 * ========================================================== */

/* ----------------------------------------------------------
 * Elements
 * ---------------------------------------------------------- */

const choicesInstances = {};

const variantForm = document.getElementById("variant-form");
const variantLists = document.querySelectorAll(".js-variant-list");
const addVariantButton = document.getElementById("add-variant");
const completeForm = document.getElementById(
    "step2-complete-form"
);

const tabs = document.querySelectorAll(".pw-tab");

const panels = document.querySelectorAll(".pw-tab-panel");

const bulkVariantForm = document.getElementById("bulk-variant-form");

const bulkAddVariantButton = document.getElementById("bulk-add-variant");

const BULK_VARIANT_WARNING_LIMIT = 50;
const BULK_VARIANT_DANGER_LIMIT = 200;

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

                cookieValue = decodeURIComponent(
                    c.substring(name.length + 1)
                );

                break;

            }

        }

    }

    return cookieValue;

}

const csrftoken = getCookie("csrftoken");





/* ----------------------------------------------------------
 * Helpers
 * ---------------------------------------------------------- */

function refreshVariantList(html, count) {

    if (variantLists.length === 0) {
        return;
    }

    variantLists.forEach(list => {
        list.innerHTML = html;
    });

    document
        .querySelectorAll(".js-variant-count")
        .forEach(counter => {
            counter.textContent = count;
        });

    const totalCounter =
        document.getElementById("variant-total-count");

    if (totalCounter) {
        totalCounter.textContent = count;
    }

    document.querySelectorAll(".js-delete-all-variants").forEach(btn => {
        if (count == 0) {
            btn.style.display = "none";
        } else {
            btn.style.display = "inline-flex";
        }
    });

}



function clearVariantInputs(form) {

    form.querySelectorAll("select[data-attribute-id]").forEach(select => {
            if (choicesInstances[select.name]) {
                choicesInstances[select.name].setChoiceByValue('');
            } else {
                select.selectedIndex = 0;
            }
        });

    form
        .querySelectorAll(".pw-custom-value")
        .forEach(input => {

            input.value = "";

        });

    form
        .querySelectorAll(".is-invalid")
        .forEach(input => {

            input.classList.remove("is-invalid");

        });

    if (form === variantForm) {
        updateCreateButtonState(form, addVariantButton);
    } else if (form === bulkVariantForm) {
        // Bulk form temizlenince checkboxları kaldır ve sayacı sıfırla
        form.querySelectorAll('.pw-checkbox-list input[type="checkbox"]').forEach(cb => cb.checked = false);
        updateCombinationCount(form, "bulk-combination-count");
    }

}

function updateCombinationCount(form, counterId) {
    const counter = document.getElementById(counterId);
    let total = 0;

    if (form === bulkVariantForm) {
        total = 1;
        let hasCheckedGroup = false;

        form.querySelectorAll(".pw-checkbox-list").forEach(list => {
            const checked = list.querySelectorAll('input[type="checkbox"]:checked').length;
            if (checked > 0) {
                total *= checked;
                hasCheckedGroup = true;
            }
        });

        if (!hasCheckedGroup) total = 0; // Hiçbir şey seçilmediyse 1 yerine 0 göstersin
    }

    if (counter) {
        counter.textContent = total;
    }

    // Buton durumlarını güncelle
    if (form === variantForm) {
        updateCreateButtonState(form, addVariantButton);
    }
    if (form === bulkVariantForm) {
        updateCreateButtonState(form, bulkAddVariantButton);
    }
}


function updateCreateButtonState(form, button) {
    if (!button) {
        return;
    }

    let hasSelection = false;

    if (form === bulkVariantForm) {
        // Toplu form için checkbox kontrolü
        form.querySelectorAll('.pw-checkbox-list input[type="checkbox"]:checked').forEach(() => {
            hasSelection = true;
        });
    } else {
        // Tekli form için select ve input kontrolü
        form.querySelectorAll('select[data-attribute-id]').forEach(select => {
            if (select.value && select.value.trim() !== "") hasSelection = true;
        });
        form.querySelectorAll('.pw-custom-value').forEach(input => {
            if (input.value && input.value.trim() !== "") hasSelection = true;
        });
    }

    button.disabled = !hasSelection;
}

/* ----------------------------------------------------------
 * Custom Attribute UX
 * ---------------------------------------------------------- */

function bindCustomInputs(form) {

    form
        .querySelectorAll(".pw-custom-value")
        .forEach(input => {

            input.addEventListener(
                "input",

                function () {

                    const attributeId =
                        this.dataset.attributeId;

                    const select =
                        form.querySelector(
                            `[name="attribute_${attributeId}"]`
                        );

                    if (
                        select &&
                        this.value.trim() !== ""
                    ) {

                            if (choicesInstances[select.name]) {
                                choicesInstances[select.name].setChoiceByValue(''); // Seçinize geri döndürür
                            } else {
                                select.selectedIndex = 0;
                            }


                    }

                    const targetButton = (form === bulkVariantForm) ? bulkAddVariantButton : addVariantButton;
                    updateCreateButtonState(form, targetButton);

                }

            );

        });


    form
        .querySelectorAll("select[data-attribute-id]")
        .forEach(select => {

            select.addEventListener(
                "change",

                function () {

                    const attributeId =
                        this.dataset.attributeId;

                    const input =
                        form.querySelector(
                            `[name="custom_attribute_${attributeId}"]`
                        );

                    if (
                        input &&
                        this.value
                    ) {

                        input.value = "";

                    }

                    const targetButton = (form === bulkVariantForm) ? bulkAddVariantButton : addVariantButton;
                    updateCreateButtonState(form, targetButton);

                }

            );

        });

}

function bindCheckboxes(form, counterId) {

    form
        .querySelectorAll(
            '.pw-checkbox-list input[type="checkbox"]'
        )
        .forEach(input => {

            input.addEventListener(
                "change",
                () => updateCombinationCount(form, counterId)

            );

        });

}

function bindCustomToggle(form) {
    form.querySelectorAll(".pw-custom-wrapper").forEach(wrapper => {
        const toggleBtn = wrapper.querySelector(".js-toggle-custom");
        const cancelBtn = wrapper.querySelector(".js-cancel-custom");
        const box = wrapper.querySelector(".pw-custom-box");
        const input = box?.querySelector("input");

        if (!toggleBtn || !box) return;

        // Gizli butona (Choices içinden) tıklandığında
        toggleBtn.addEventListener("click", () => {
            box.hidden = false;
            box.style.opacity = "0";
            setTimeout(() => box.style.opacity = "1", 10); // Fade-in animasyonu
            if (input) input.focus();
        });

        // "İptal" butonuna tıklanınca
        if (cancelBtn) {
            cancelBtn.addEventListener("click", () => {
                box.hidden = true;
                if (input) {
                    input.value = "";
                    input.dispatchEvent(new Event("input"));
                }
            });
        }
    });
}

/* ----------------------------------------------------------
 * Checkbox Filter (Hızlı Arama)
 * ---------------------------------------------------------- */
function initCheckboxFilters() {
    const filterInputs = document.querySelectorAll('.pw-filter-input');

    filterInputs.forEach(input => {
        const listId = input.dataset.listId;
        const list = document.getElementById(listId);

        if (!list) return;

        const items = list.querySelectorAll('.pw-checkbox-item');

        // Eğer 10'dan az seçenek varsa arama kutusu gizli kalsın.
        if (items.length > 10) {
            input.parentElement.hidden = false;
        }

        // Arama event'i
        input.addEventListener('input', function(e) {
            const term = e.target.value.toLowerCase().trim();
            let hasVisible = false;

            items.forEach(item => {
                const label = item.querySelector('.pw-checkbox-label').textContent.toLowerCase();

                // Eğer aranan kelime label içinde geçiyorsa göster, yoksa gizle
                if (label.includes(term)) {
                    item.style.display = ''; // Varsayılan flex'e döner
                    hasVisible = true;
                } else {
                    item.style.display = 'none';
                }
            });

            // Eğer hiç sonuç bulunamazsa kullanıcıya bilgi verelim
            let emptyState = list.querySelector('.pw-filter-empty');

            if (!hasVisible && term !== '') {
                if (!emptyState) {
                    emptyState = document.createElement('div');
                    emptyState.className = 'pw-empty pw-filter-empty';
                    emptyState.style.gridColumn = '1 / -1'; // Tüm sütunları kaplasın
                    emptyState.textContent = 'Eşleşen seçenek bulunamadı.';
                    list.appendChild(emptyState);
                } else {
                    emptyState.style.display = 'block';
                }
            } else if (emptyState) {
                emptyState.style.display = 'none';
            }
        });
    });
}


/* ----------------------------------------------------------
 * Create Variant
 * ---------------------------------------------------------- */



async function createVariant({
    form,
    button,
    counterId,
}) {

    if (form === bulkVariantForm && counterId) {
        const combinationCounter = document.getElementById(counterId);
        const combinationCount = Number(combinationCounter?.textContent || 0);

        if (combinationCount > BULK_VARIANT_WARNING_LIMIT) {
            let message = `${combinationCount} adet varyant oluşturulacak.`;

            if (combinationCount >= BULK_VARIANT_DANGER_LIMIT) {
                message += "\n\nBu sayı oldukça yüksek. İşlem biraz uzun sürebilir.";
            } else {
                message += "\n\nDevam etmek istiyor musunuz?";
            }

            const confirmed = await WizardUI.showConfirm({
                title: "Çok fazla varyant oluşturuluyor!",
                message: message,
                confirmText: "Yine de Oluştur"
            });

            if (!confirmed) {
                return; // Kullanıcı vazgeçtiyse işlemi durdur
            }
        }
    }

    const loadingMessage =
        form === bulkVariantForm
            ? "Varyantlar oluşturuluyor..."
            : "Varyant ekleniyor...";

    const loadingToast =
        WizardUI.showLoading(loadingMessage);

    if (!button.dataset.url) {
        WizardUI.showToast(
            "error",
            "URL bulunamadı."
        );

        return;

    }

    const url =
        button.dataset.url;


    if (button.disabled) {
        return;
    }

    const formData = new FormData(
        form
    );

    WizardUI.setButtonLoading(
        button,
        true,
        form === bulkVariantForm
            ? "Oluşturuluyor..."
            : "Ekleniyor..."
    );

    variantLists.forEach(list => list.style.opacity = "0.5");

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

        try {

            data = await response.json();

        }
        catch {

            data = {};

        }

        if (!response.ok || !data.success) {

            if (data.errors) {

                const messages = Object.values(data.errors)
                    .flat();

                WizardUI.showToast(
                    "error",
                    messages.join("\n")
                );

                return;

            }

            WizardUI.showToast(
                "error",
                data.message ||
                "Varyant oluşturulamadı."
            );

            return;

        }

        refreshVariantList(data.html, data.count);

        WizardUI.setButtonLoading(button, false);

        clearVariantInputs(form);

        updateCombinationCount(
                form,
                counterId,
            );


        WizardUI.showToast(
            "success",
            data.message
        );

    }

    catch (error) {

        console.error(error);

        WizardUI.showToast(
            "error",
            "Beklenmeyen bir hata oluştu."
        );

    }

    finally {


        WizardUI.setButtonLoading(
            button,
            false
        );

        if (loadingToast) {
            loadingToast.close();
        }

        variantLists.forEach(list => list.style.opacity = "1");

    }

}


/* ----------------------------------------------------------
 * Delete Variant
 * ---------------------------------------------------------- */

async function deleteVariant(button) {


    if (button.disabled) {
        return;
    }


    if (!button.dataset.url) {
        WizardUI.showToast(
            "error",
            "Silme URL'si bulunamadı."
        );

        return;
    }

    const confirmed = await WizardUI.showConfirm({
        title:"Varyant silinsin mi?",
        message:"Bu işlem geri alınamaz.",
        confirmText:"Sil"
    });

    if (!confirmed) {
        return;
    }

    WizardUI.setButtonLoading(
        button,
        true,
        "Siliniyor..."
    );

    variantLists.forEach(list => list.style.opacity = "0.5");

    try {

        const response = await fetch(
            button.dataset.url,

            {

                method: "POST",

                headers: {

                    "X-CSRFToken": csrftoken,
                    "X-Requested-With": "XMLHttpRequest",

                },

            }

        );

        let data = {};

        try {

            data = await response.json();

        }
        catch {

            data = {};

        }

    if (!response.ok || !data.success) {

        if (data.errors) {

            const messages = Object.values(data.errors)
                .flat();

            WizardUI.showToast(
                "error",
                messages.join("\n")
            );

            return;

        }

        WizardUI.showToast(
            "error",
            data.message ||
            "Varyant silinemedi."
        );

        return;

    }

    refreshVariantList(data.html, data.count);

    WizardUI.showToast(
        "success",
        data.message
    );

    }

    catch (error) {

        console.error(error);

        WizardUI.showToast(
            "error",
            "Beklenmeyen bir hata oluştu."
        );

    }
    finally {

        WizardUI.setButtonLoading(
        button,
        false
    );
    }

    variantLists.forEach(list => list.style.opacity = "1");


}

async function completeStep2() {



    if (!completeForm) {
        return;
    }

    const submitButton = document.querySelector(
        'button[form="step2-complete-form"]'
    );

    if (!submitButton || submitButton.disabled) {
        return;
    }


    WizardUI.setButtonLoading(
        submitButton,
        true,
        "Devam Ediliyor..."
    );

    const formData = new FormData(completeForm);

    try {

        const response = await fetch(
            completeForm.action,
            {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrftoken,
                    "X-Requested-With": "XMLHttpRequest",
                },
                body: formData,
            }
        );

        let data = {};

        try {
            data = await response.json();
        }
        catch {
            data = {};
        }

        if (!response.ok || !data.success) {

            WizardUI.showToast(
                "error",
                data.message ||
                "Devam edilirken bir hata oluştu."
            );

            return;
        }


        if (!data.redirect_url) {
            WizardUI.showToast(
                "error",
                "Yönlendirme adresi bulunamadı."
            );
            return;

        }
        window.location.href = data.redirect_url;

    }

    catch (error) {

        console.error(error);

        WizardUI.showToast(
            "error",
            "Sunucuya ulaşılamadı."
        );
    }

    finally {

        WizardUI.setButtonLoading(
            submitButton,
            false
        );

    }
}


/* ----------------------------------------------------------
 * Events
 * ---------------------------------------------------------- */

function handleVariantDelete(event) {

    const button =
        event.target.closest(".pw-remove");

    if (!button) {
        return;
    }

    deleteVariant(button);

}

/* ----------------------------------------------------------
 * Delete All Variants (Tümünü Sil)
 * ---------------------------------------------------------- */
document.querySelectorAll(".js-delete-all-variants").forEach(btn => {
    btn.addEventListener("click", async function() {
        const url = this.dataset.url;

        if (!url || this.disabled) return;

        // 1. Güçlü Onay Al
        const confirmed = await WizardUI.showConfirm({
            title: "Tüm varyantlar silinsin mi?",
            message: "Bu taslağa ait oluşturduğunuz tüm varyantlar kalıcı olarak silinecektir. Bu işlem geri alınamaz.",
            confirmText: "Evet, Tümünü Sil"
        });

        if (!confirmed) return;

        // 2. Animasyon & Loading
                const originalHtml = this.innerHTML;
        WizardUI.setButtonLoading(this, true, "Siliniyor...");


        // Listenin opaklığını düşür
        variantLists.forEach(list => list.style.opacity = "0.5");

        // 3. İstek At
        try {
            const response = await fetch(url, {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrftoken,
                    "X-Requested-With": "XMLHttpRequest",
                }
            });

            let data = {};
            try { data = await response.json(); } catch {}

            if (!response.ok || !data.success) {
                WizardUI.showToast("error", data.message || "Silme işlemi başarısız oldu.");
                return;
            }

            // 4. Başarılı ise UI Güncelle
            refreshVariantList(data.html, data.count);
            WizardUI.showToast("success", data.message);

        } catch (error) {
            console.error(error);
            WizardUI.showToast("error", "Beklenmeyen bir hata oluştu.");
        } finally {
            // Durumları geri yükle
            WizardUI.setButtonLoading(this, false);
            this.innerHTML = originalHtml;
            variantLists.forEach(list => list.style.opacity = "1");
        }
    });
});


/* ----------------------------------------------------------
 * Bulk Actions (Select All / Clear)
 * ---------------------------------------------------------- */
function bindBulkActions(form, counterId) {
    if (!form) return;

    form.addEventListener("click", function (event) {
        // Tümünü Seç veya Temizle butonuna tıklandıysa
        if (event.target.matches(".js-select-all") || event.target.matches(".js-clear-all")) {
            event.preventDefault();

            const isSelectAll = event.target.matches(".js-select-all");
            const attributeContainer = event.target.closest(".pw-attribute");

            if (attributeContainer) {
                // Sadece o özelliğin içindeki checkbox'ları bul
                const checkboxes = attributeContainer.querySelectorAll('.pw-checkbox-list input[type="checkbox"]');

                // İşaretle veya işaretini kaldır
                checkboxes.forEach(cb => {
                    cb.checked = isSelectAll;
                });

                // Kombinasyon sayacını ve butonu güncelle
                updateCombinationCount(form, counterId);
            }
        }
    });
}


/* ----------------------------------------------------------
 * Init
 * ---------------------------------------------------------- */

document.addEventListener(
    "DOMContentLoaded",


    function () {

        // Selectleri Modern Choices'a Çevir ve Menü İçine Buton Ekle
        document.querySelectorAll('select.pw-select').forEach(select => {
            const instance = new Choices(select, {
                searchEnabled: true,
                searchPlaceholderValue: 'Ara...',
                itemSelectText: '',
                noResultsText: 'Sonuç bulunamadı',
                noChoicesText: 'Seçenek yok',
                shouldSort: false
            });
            choicesInstances[select.name] = instance;

            // Kapsayıcıyı ve İsim değerini bul
            const container = select.closest('.pw-attribute-grid');
            if (!container) return;

            const wrapper = container.querySelector('.pw-custom-wrapper');
            const attrNameContainer = container.querySelector('.js-select-container');
            const attrName = attrNameContainer ? attrNameContainer.dataset.attrName : "Değer";

            if (wrapper) {
                const toggleBtn = wrapper.querySelector('.js-toggle-custom');
                const customBox = wrapper.querySelector('.pw-custom-box');

                // Choices.js DOM'u oluşturduktan sonra ana dropdown kabını buluyoruz
                const choicesDiv = select.closest('.choices');
                const dropdownList = choicesDiv.querySelector('.choices__list--dropdown');

                if (dropdownList) {
                    // Butonu Dropdown'un en altına SABİT olarak ekliyoruz (Her açılışta eklenmez, 1 kere eklenir)
                    const btnHtml = `
                        <div class="js-choices-custom-btn" style="border-top: 1px solid var(--gray-200); padding: 12px 14px; background: #fafafa; cursor: pointer; display: flex; align-items: center; gap: 8px; color: var(--teal-600); font-weight: 600; font-size: 0.95rem; transition: background .2s ease;">
                            <i class="fa-solid fa-plus"></i> Yeni ${attrName} Ekle
                        </div>
                    `;

                    dropdownList.insertAdjacentHTML('beforeend', btnHtml);
                    const newBtn = dropdownList.querySelector('.js-choices-custom-btn');

                    // Hover efektleri
                    newBtn.addEventListener('mouseenter', () => newBtn.style.backgroundColor = 'var(--teal-50)');
                    newBtn.addEventListener('mouseleave', () => newBtn.style.backgroundColor = '#fafafa');

                    // KRİTİK NOKTA: Mousedown olayı Choices'in odak (focus) kaybetmesini ve menüyü kapatmasını engeller
                    newBtn.addEventListener('mousedown', function(e) {
                        e.preventDefault();
                    });

                    // Butona Tıklanınca
                    newBtn.addEventListener('click', function(e) {
                        e.preventDefault();
                        e.stopPropagation();

                        instance.hideDropdown(); // Choices menüsünü güvenlice kapat
                        instance.setChoiceByValue(''); // Seçili değeri temizle

                        // Textbox kutusunu aç
                        if (toggleBtn) toggleBtn.click();
                    });
                }

                // Kullanıcı listeden mevcut bir değeri seçerse:
                select.addEventListener('change', function() {
                    // Eğer Textbox açıksa onu kapat ve içini temizle
                    if (select.value && customBox && !customBox.hidden) {
                        const cancelBtn = customBox.querySelector('.js-cancel-custom');
                        if (cancelBtn) cancelBtn.click();
                    }
                });
            }
        });

        if (
            variantLists.length === 0 ||
            !completeForm
        ) {
            return;
        }

        initializeTabs();
        initCheckboxFilters();


        bindCustomInputs(variantForm);
        bindCheckboxes(variantForm, "single-combination-count");
        updateCombinationCount(variantForm, "single-combination-count");

        bindCustomToggle(variantForm);

        if (bulkVariantForm) {
            bindCustomToggle(bulkVariantForm);
        }

        if (bulkVariantForm) {

            bindCustomInputs(bulkVariantForm);

            bindCheckboxes(
                bulkVariantForm,
                "bulk-combination-count",
            );

            updateCombinationCount(
                bulkVariantForm,
                "bulk-combination-count",
            );

            bindBulkActions(
                bulkVariantForm,
                "bulk-combination-count"
            );

        }



        addVariantButton.addEventListener(
            "click",
            () => createVariant({
                form: variantForm,
                button: addVariantButton,
                counterId: "single-combination-count",
            })
        );

        if (
            bulkVariantForm &&
            bulkAddVariantButton
        ) {

            bulkAddVariantButton.addEventListener(
                "click",

                () => createVariant({
                    form: bulkVariantForm,
                    button: bulkAddVariantButton,
                    counterId: "bulk-combination-count",
                })

            );

        }


        variantLists.forEach(list => {

            list.addEventListener(
                "click",
                handleVariantDelete
            );

        });

        completeForm?.addEventListener(
            "submit",
            function (event) {

                event.preventDefault();

                completeStep2();

            }
        );

    }


);

/* ----------------------------------------------------------
 * Tabs
 * ---------------------------------------------------------- */

function initializeTabs() {
    if (tabs.length <= 1) {
        return;
    }

    // 1. Sayfa yüklendiğinde localStorage'dan kayıtlı sekmeyi oku
    const savedTab = localStorage.getItem("variantWizardTab");

    if (savedTab) {
        const savedButton = document.querySelector(`.pw-tab[data-target="${savedTab}"]`);
        const savedPanel = document.getElementById(`${savedTab}-panel`);

        if (savedButton && savedPanel) {
            // Tüm sekmeleri ve panelleri temizle
            tabs.forEach(tab => tab.classList.remove("active"));
            panels.forEach(panel => panel.classList.remove("active"));

            // Kaydedilen sekmeyi ve paneli aktif et
            savedButton.classList.add("active");
            savedPanel.classList.add("active");
        }
    }

    // 2. Sekme değiştirme (Click) event'i
    tabs.forEach(tab => {
        tab.addEventListener("click", () => {
            const target = tab.dataset.target;

            // Kullanıcının tercihini kaydet
            localStorage.setItem("variantWizardTab", target);

            // Tüm sekmeleri ve panelleri temizle
            tabs.forEach(item => item.classList.remove("active"));
            panels.forEach(panel => panel.classList.remove("active"));

            // Tıklanan sekmeyi ve hedeflenen paneli aktif et
            tab.classList.add("active");

            const activePanel = document.getElementById(`${target}-panel`);
            if (activePanel) {
                activePanel.classList.add("active");
            }
        });
    });
}
