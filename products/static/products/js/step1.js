
document.addEventListener("DOMContentLoaded", () => {

    function getCookie(name) {

        let cookieValue = null;

        if (document.cookie && document.cookie !== "") {

            const cookies =
                document.cookie.split(";");


            for (let cookie of cookies) {

                cookie = cookie.trim();


                if (cookie.startsWith(name + "=")) {

                    cookieValue =
                        decodeURIComponent(
                            cookie.substring(
                                name.length + 1
                            )
                        );

                    break;
                }
            }
        }

        return cookieValue;
    }

    const parentSelect = document.getElementById("id_parent_category");
    const categorySelect = document.getElementById("id_category");
    const brandSelect = document.getElementById("id_brand");

    const brandRequestLink = document.getElementById("request-brand-link");
    const brandModal = document.getElementById("brand-request-modal");
    const closeBrandModal = document.getElementById("close-brand-modal");
    const cancelBrandRequest = document.getElementById("cancel-brand-request");
    const brandRequestForm = document.getElementById("brand-request-form");

    const api = document.getElementById("wizard-api");

    const wizardForm = document.getElementById(
        "wizard-step1-form"
    );
    
    const duplicateModal = document.getElementById(
        "duplicate-product-modal"
    );
    
    const closeDuplicateModal = document.getElementById(
        "close-duplicate-modal"
    );
    
    const cancelDuplicateModal = document.getElementById(
        "cancel-duplicate-modal"
    );
    
    const continueNewProductBtn = document.getElementById(
        "continue-new-product"
    );
    
    const useExistingProductBtn = document.getElementById(
        "use-existing-product-btn"
    );
    
    const matchBox = document.getElementById(
        "match-box"
    );
    
    const matchProductName = document.getElementById(
        "match-product-name"
    );
    
    const matchProductBrand = document.getElementById(
        "match-product-brand"
    );
    
    const matchProductCategory = document.getElementById(
        "match-product-category"
    );

 

    if (
        !parentSelect ||
        !categorySelect ||
        !brandSelect ||
        !api
    ) {
        return;
    }


    const categoryUrl = api.dataset.categoryUrl;
    const brandUrl = api.dataset.brandUrl;
    const brandRequestUrl = api.dataset.brandRequestUrl;
    const matchActionUrl = api.dataset.matchActionUrl;

    if (
        !categoryUrl ||
        !brandUrl ||
        !brandRequestUrl ||
        !matchActionUrl
    ) {
        return;
    }



    function resetSelect(select, placeholder) {

        select.innerHTML = "";

        const option = document.createElement("option");

        option.value = "";
        option.textContent = placeholder;

        option.selected = true;

        select.appendChild(option);

    }

    function populateSelect(select, items, selectedValue = "") {

        items.forEach(item => {

            const option = document.createElement("option");

            option.value = item.id;
            option.textContent = item.name;

            if (
                selectedValue &&
                String(item.id) === String(selectedValue)
            ) {
                option.selected = true;
            }

            select.appendChild(option);

        });

        if (!selectedValue) {
            select.selectedIndex = 0;
        }

    }

    let categoryFetchController = null;
    let brandFetchController = null;
    async function loadCategories(selectedCategory = null) {
        // Eğer devam eden bir istek varsa iptal et
        if (categoryFetchController) {
            categoryFetchController.abort();
        }
        // Yeni bir controller oluştur
        categoryFetchController = new AbortController();

        const parentId = parentSelect.value;

        if (!parentId) {
            resetSelect(categorySelect, "Alt kategori seçiniz");
            resetSelect(brandSelect, "Önce alt kategori seçiniz");
            return;
        }

        resetSelect(categorySelect, "Alt kategori seçiniz");
        resetSelect(brandSelect, "Önce alt kategori seçiniz");

        try {
            const url = categoryUrl.replace("__id__", parentId);

            // 2. signal parametresini fetch'e ekle
            const response = await fetch(url, {
                headers: {"X-Requested-With": "XMLHttpRequest"},
                signal: categoryFetchController.signal 
            });

            if (!response.ok) {
                throw new Error("Alt kategoriler alınamadı.");
            }

            let data = await response.json().catch(() => ({}));
            if (!data.results || !data.results.length){
                resetSelect(categorySelect, "Alt kategori bulunamadı");
                return;
            }

            resetSelect(categorySelect, "Alt kategori seçiniz");
            populateSelect(categorySelect, data.results, selectedCategory);
        }
        catch (error) {
            // 3. Kullanıcı yeni seçim yaptığı için iptal edilen istekleri hata olarak gösterme
            if (error.name === 'AbortError') {
                return; // Sessizce çık
            }
            console.error(error);
        }
    }

    async function loadBrands(selectedBrand = "") {

        // 1. Eğer devam eden bir istek varsa iptal et
        if (brandFetchController) {
            brandFetchController.abort();
        }
        // Yeni bir controller oluştur
        brandFetchController = new AbortController();

        const categoryId = categorySelect.value;

        if (!categoryId) {
            resetSelect(brandSelect, "Marka seçiniz (Opsiyonel)");
            return;
        }

        resetSelect(brandSelect, "Marka seçiniz (Opsiyonel)");

        try {
            const url = brandUrl.replace("__id__", categoryId);

            // 2. signal parametresini fetch'e ekle
            const response = await fetch(url, {
                headers: {"X-Requested-With": "XMLHttpRequest"},
                signal: brandFetchController.signal
            });

            if (!response.ok) {
                throw new Error("Markalar alınamadı.");
            }

            let data = await response.json().catch(() => ({}));
            if (!data.results || !data.results.length){
                resetSelect(brandSelect, "Bu kategoriye ait marka bulunamadı");
            } else {
                resetSelect(brandSelect, "Marka seçiniz (Opsiyonel)");
                populateSelect(brandSelect, data.results, selectedBrand);
            }
        }
        catch(error){
            // 3. Kullanıcı yeni seçim yaptığı için iptal edilen istekleri hata olarak gösterme
            if (error.name === 'AbortError') {
                return; // Sessizce çık
            }
            console.error(error);
        }
    }



    parentSelect.addEventListener("change", async () => {

        categorySelect.value = "";

        brandSelect.value = "";

        await loadCategories();

        if (brandModal && !brandModal.hidden) {
            closeBrandModalWindow();
        }

    });

    categorySelect.addEventListener(
        "change",
        async () => {

            await loadBrands();

            if (
                brandModal &&
                !brandModal.hidden
            ){
                closeBrandModalWindow();
            }

            if(brandRequestForm){
                delete brandRequestForm.dataset.categoryId;
            }

        }
    );


    /*
    ------------------------------------------
    Sayfa hata sonrası yeniden açıldıysa
    seçimleri geri yükle
    ------------------------------------------
    */

    (async () => {
        const selectedCategory = categorySelect.value;
        const selectedBrand = brandSelect.value;

        if (parentSelect.value) {
            // SADECE Django select içine seçenekleri KOYMADIYSA (length <= 1) fetch at
            if (categorySelect.options.length <= 1) {
                await loadCategories(selectedCategory);
            }
            if (categorySelect.value && brandSelect.options.length <= 1) {
                await loadBrands(selectedBrand);
            }
        }
    })();


    /*
    ---------------------------------------
    Brand Request Modal
    ---------------------------------------
    */
    
    brandRequestLink?.addEventListener("click", (event) => {

        event.preventDefault();

        if (!brandRequestUrl) {
            WizardUI.showToast(
                "error",
                "Marka talep sistemi şu anda kullanılamıyor."
            );
            return;
        }

        if (!brandModal || !brandRequestForm) {
            return;
        }

        if (!categorySelect.value) {
            WizardUI.showToast(
                "error",
                "Önce alt kategori seçiniz."
            );
            return;
        }

        
        brandRequestForm.reset();
        brandRequestForm.dataset.categoryId = categorySelect.value;



        brandModal.hidden = false;
        document.body.classList.add("modal-open");




    });



    function closeBrandModalWindow() {

        if (brandModal) {
            brandModal.hidden = true;
            document.body.classList.remove("modal-open");
        }

        if (brandRequestForm) {
            brandRequestForm.reset();

            delete brandRequestForm.dataset.categoryId;
        }
    }


    /*
    ---------------------------------------
    Duplicate Product Modal
    ---------------------------------------
    */
        
    function openDuplicateModal(match) {
    
        if (!duplicateModal) {
            return;
        }
    
        matchProductName.textContent =
            match.name || "-";
    
        matchProductBrand.textContent =
            match.brand || "-";
    
        matchProductCategory.textContent =
            match.category || "-";
    
        if (matchBox) {
            matchBox.hidden = false;
        }

    
        duplicateModal.hidden = false;
        document.body.classList.add("modal-open");
    
    }
    
    
    function closeDuplicateModalWindow() {
    
        if (!duplicateModal) {
            return;
        }
    
        duplicateModal.hidden = true;
        document.body.classList.remove("modal-open");
    
        if (matchBox) {
            matchBox.hidden = true;
        }
    
        matchProductName.textContent = "-";
        matchProductBrand.textContent = "-";
        matchProductCategory.textContent = "-";
    
        delete useExistingProductBtn.dataset.draftId;

        WizardUI.setButtonLoading(
            useExistingProductBtn,
            false,
        );

        WizardUI.setButtonLoading(
            continueNewProductBtn,
            false,
        );

        if (cancelDuplicateModal) cancelDuplicateModal.disabled = false;
        if (closeDuplicateModal) closeDuplicateModal.disabled = false;

    
    }

    async function sendMatchDecision(decision) {

        const draftId = useExistingProductBtn.dataset.draftId;

        if (!draftId) {
            return;
        }

        WizardUI.setButtonLoading(
            useExistingProductBtn,
            true,
            "Yükleniyor..."
        );

        WizardUI.setButtonLoading(
            continueNewProductBtn,
            true,
            "Yükleniyor..."
        );

        if(cancelDuplicateModal) cancelDuplicateModal.disabled = true;
        if(closeDuplicateModal) closeDuplicateModal.disabled = true;

        const url = matchActionUrl.replace(
            "__id__",
            draftId,
        );

        const formData = new FormData();

        formData.append(
            "decision",
            decision,
        );

        try {

            const response = await fetch(
                url,
                {
                    method: "POST",
                    headers: {
                        "X-CSRFToken": getCookie("csrftoken"),
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    body: formData,
                }
            );


            let data = await response.json().catch(() => ({}));

            if (!response.ok) {
                WizardUI.showToast(
                    "error",
                    "Bir hata oluştu."
                );
                closeDuplicateModalWindow();
                return;
            }

            window.location.href = data.redirect_url;

        }

        catch (error) {

            console.error(error);

            WizardUI.showToast(
                "error",
                "Sunucuya ulaşılamadı"
            );

            closeDuplicateModalWindow();

        }

    }

    

    duplicateModal?.addEventListener(
        "click",
        (event) => {

            if (
                event.target === duplicateModal &&
                !cancelDuplicateModal.disabled
            ) {
                closeDuplicateModalWindow();
            }

        }
    );

    closeDuplicateModal?.addEventListener(
        "click",
        closeDuplicateModalWindow,
    );

    cancelDuplicateModal?.addEventListener(
        "click",
        closeDuplicateModalWindow,
    );

    brandModal?.addEventListener("click", (event) => {

        if (event.target === brandModal) {
            closeBrandModalWindow();
        }

    });

    closeBrandModal?.addEventListener(
        "click",
        closeBrandModalWindow,
    );

    cancelBrandRequest?.addEventListener(
        "click",
        closeBrandModalWindow,
    );

    document.addEventListener("keydown", (event) => {

        if (event.key !== "Escape") {
            return;
        }

        if (
            brandModal &&
            !brandModal.hidden
        ) {
            closeBrandModalWindow();
        }

        if (
            duplicateModal &&
            !duplicateModal.hidden &&
            !cancelDuplicateModal.disabled
        ) {
            closeDuplicateModalWindow();
        }

    });



    brandRequestForm?.addEventListener(
        "submit",
        async (event) => {

            event.preventDefault();

            const submitButton =
                brandRequestForm.querySelector(
                    'button[type="submit"]'
                );

            if (!submitButton) {
                return;
            }

            WizardUI.setButtonLoading(
                submitButton,
                true,
                "Gönderiliyor..."
            );


            const categoryId =
                brandRequestForm.dataset.categoryId;
            
            if (!categoryId) {
                return;
            }

            const url =
                brandRequestUrl.replace(
                    "__id__",
                    categoryId,
                );

            const formData =
                new FormData(brandRequestForm);

            try {

                const response = await fetch(
                    url,
                    {
                        method: "POST",
                        headers: {
                            "X-Requested-With":
                                "XMLHttpRequest",
                            "X-CSRFToken": getCookie("csrftoken"),
                        },
                        body: formData,
                    }
                );

                let data = await response.json().catch(() => ({}));

                if (!response.ok || !data.success) {
                    if (data.errors) {
                        const messages = Object.values(data.errors).flat();
                        WizardUI.showToast("error", messages.join("\n"));
                        return;
                    }
                    WizardUI.showToast("error", data.message || "Bir hata oluştu.");
                    return;
                }

                WizardUI.showToast(
                    "success",
                    data.message
                );

                brandRequestForm.reset();


                setTimeout(() => {
                
                    closeBrandModalWindow();
                
                }, 1800);

            }

            catch (error) {

                console.error(error);

                WizardUI.showToast(
                    "error",
                    "Sunucuya ulaşılamadı."
                );

            }

            finally{
                WizardUI.setButtonLoading(
                    submitButton,
                    false
                );
            
            }

        }
    );

    useExistingProductBtn?.addEventListener(
        "click",
        () => sendMatchDecision("accept"),
    );

    continueNewProductBtn?.addEventListener(
        "click",
        () => sendMatchDecision("reject"),
    );


    async function submitWizardForm(extraData = {}) {

        if (!wizardForm) {
            return;
        }

        const submitButton =
            wizardForm.querySelector(
                'button[type="submit"]'
            );

        if (!submitButton) {
            return;
        }

        let shouldResetLoading = true;

        WizardUI.setButtonLoading(
            submitButton,
            true,
            "Kaydediliyor..."
        );

        const formData = new FormData(
            wizardForm,
        );



        Object.entries(extraData).forEach(
            ([key, value]) => {
                formData.set(key, value);
            }
        );


        try {

            const response = await fetch(
                wizardForm.action,
                {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRFToken": getCookie("csrftoken"),
                    },
                    body: formData,
                }
            );

            let data = await response.json().catch(() => ({}));

            // 1. Kategori değişim onayı
            if (response.status === 409 && data.confirm_category_change) {
                const confirmed = await WizardUI.showConfirm({
                    title: "Kategori değiştirilsin mi?",
                    message: "Kategoriyi değiştirirseniz oluşturduğunuz tüm varyantlar ve resim grupları silinecektir.",
                    confirmText: "Devam et",
                    cancelText: "Vazgeç",
                });

                if (confirmed) {
                    shouldResetLoading = false;
                    return submitWizardForm({ confirm_category_change: 1 });
                } 
                return;
            }
            // 2. Form validation hataları
            if (!response.ok || !data.success) {
                if (data.errors) {
                    const messages = Object.values(data.errors).flat();
                    WizardUI.showToast("error", messages.join("\n"));
                } else {
                    WizardUI.showToast("error", data.message || "Bir hata oluştu.");
                }
                return;
            }
            // 3. Duplicate modal
            if (data.duplicate) {
                useExistingProductBtn.dataset.draftId = data.draft_id;
                

                // Kullanıcı iptale basarsa, sonraki form gönderiminde "yeni kayıt" yerine "güncelleme"
                // yapması için draft_id'yi formun içine gizli bir input olarak ekliyoruz.
                let hiddenDraftInput = wizardForm.querySelector('input[name="draft_id"]');
                if (!hiddenDraftInput) {
                    hiddenDraftInput = document.createElement('input');
                    hiddenDraftInput.type = 'hidden';
                    hiddenDraftInput.name = 'draft_id';
                    wizardForm.appendChild(hiddenDraftInput);
                }
                hiddenDraftInput.value = data.draft_id;


                openDuplicateModal(data.match);
                return;
            }
            // 4. Normal redirect
            shouldResetLoading = false;
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

            if (shouldResetLoading) {
                WizardUI.setButtonLoading(
                    submitButton,
                    false,
                );
            }

        }

    }

    wizardForm?.addEventListener("submit", async (event) => {
        event.preventDefault();
        await submitWizardForm();
    });

});

