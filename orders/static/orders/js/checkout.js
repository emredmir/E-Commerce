document.addEventListener("DOMContentLoaded", function () {

    // =========================================================
    // ELEMENTS
    // =========================================================

    const sameBillingCheckbox =
        document.getElementById("use_same_billing");

    const billingSection =
        document.getElementById(
            "billing-addresses-wrapper"
        );

    const paymentButton =
        document.getElementById(
            "checkout-payment-button"
        );

    const newCardForm =
        document.getElementById(
            "new-card-form"
        );

    const saveNewCardInput =
        document.getElementById(
            "save-new-card"
        );

    const storedCardForm =
        document.getElementById(
            "stored-card-form"
        );

    const shippingRadios = Array.from(
        document.querySelectorAll(
            'input[name="shipping_address_id"]'
        )
    );

    const billingRadios = Array.from(
        document.querySelectorAll(
            'input[name="billing_address_id"]'
        )
    );

    const paymentMethodRadios = Array.from(
        document.querySelectorAll(
            'input[name="payment_method"]'
        )
    );

    const addressOptions = Array.from(
        document.querySelectorAll(
            ".address-option"
        )
    );

    // Card fields

    const cardHolderNameInput =
        document.getElementById(
            "card-holder-name"
        );

    const cardNumberInput =
        document.getElementById(
            "card-number"
        );

    const cardExpireMonthInput =
        document.getElementById(
            "card-expire-month"
        );

    const cardExpireYearInput =
        document.getElementById(
            "card-expire-year"
        );

    const cardCvcInput =
        document.getElementById(
            "card-cvc"
        );

    const cardCvcError =
        document.getElementById(
            "card-cvc-error"
        );

    const installmentInput =
        document.getElementById(
            "payment-installment"
        );

    const storedCardRadios = Array.from(
        document.querySelectorAll(
            'input[name="stored_card_id"]'
        )
    );


    // =========================================================
    // STATE
    // =========================================================

    /*
     * Checkbox tekrar kapatıldığında geri getirmek için
     * bağımsız fatura adresinin ID'sini saklıyoruz.
     */
    let previousBillingAddressId = null;

    /*
     * İlk order oluşturulduktan sonra aynı checkout oturumunda
     * tekrar Order oluşturmamak için tutulur.
     */
    let currentOrderNumber = null;

    let isPaymentProcessing = false;

    /*
     * Checkout sırasında kart başarıyla kaydedildiyse
     * aynı kartın tekrar oluşturulmasını engeller.
     */
    let savedCardForCurrentPaymentId = null;

    /*
     * Card Storage create operation için idempotency key.
     */
    let saveCardIdempotencyKey = null;


    // =========================================================
    // CSRF
    // =========================================================

    function getCookie(name) {

        const cookieValue =
            document.cookie
                .split("; ")
                .find(
                    function (row) {
                        return row.startsWith(
                            name + "="
                        );
                    }
                );

        if (!cookieValue) {
            return null;
        }

        return decodeURIComponent(
            cookieValue.split("=")[1]
        );
    }


    function getCsrfToken() {

        return getCookie(
            "csrftoken"
        );
    }


    // =========================================================
    // MONEY
    // =========================================================

    function parsePriceToFloat(value) {

        if (typeof value === "number") {
            return value;
        }

        if (
            value === null ||
            value === undefined ||
            value === ""
        ) {
            return 0;
        }

        let str = String(value)
            .replace(/TL/g, "")
            .trim();

        /*
         * 1.234,56
         */
        if (
            str.includes(".") &&
            str.includes(",")
        ) {
            str = str.replace(/\./g, "");
            str = str.replace(",", ".");
        }

        /*
         * 1234,56
         */
        else if (str.includes(",")) {
            str = str.replace(",", ".");
        }

        return parseFloat(str) || 0;
    }


    function formatMoneyTR(value) {

        const number =
            parsePriceToFloat(value);

        return new Intl.NumberFormat(
            "tr-TR",
            {
                minimumFractionDigits: 2,
                maximumFractionDigits: 2,
            }
        ).format(number);
    }


    function initPriceFormatting() {

        document
            .querySelectorAll(".js-format-money")
            .forEach(function (element) {

                const raw =
                    element.getAttribute(
                        "data-raw"
                    );

                if (raw === null) {
                    return;
                }

                element.textContent =
                    formatMoneyTR(raw);

            });

    }


    // =========================================================
    // ADDRESS
    // =========================================================

    function getSelectedShippingAddressId() {

        const selected =
            shippingRadios.find(
                function (radio) {
                    return radio.checked;
                }
            );

        return selected
            ? selected.value
            : null;
    }


    function getSelectedBillingAddressId() {

        const selected =
            billingRadios.find(
                function (radio) {
                    return radio.checked;
                }
            );

        return selected
            ? selected.value
            : null;
    }


    function selectBillingAddress(addressId) {

        if (!addressId) {
            return;
        }

        billingRadios.forEach(
            function (radio) {

                radio.checked =
                    String(radio.value) ===
                    String(addressId);

            }
        );

        updateAddressVisualState();
    }


    function updateAddressVisualState() {

        addressOptions.forEach(
            function (option) {

                const radio =
                    option.querySelector(
                        'input[type="radio"]'
                    );

                if (!radio) {
                    return;
                }

                option.classList.toggle(
                    "is-selected",
                    radio.checked
                );
            }
        );
    }


    // =========================================================
    // BILLING
    // =========================================================

    function syncBillingWithShipping() {

        const shippingAddressId =
            getSelectedShippingAddressId();

        if (!shippingAddressId) {
            return;
        }

        selectBillingAddress(
            shippingAddressId
        );
    }


    function updateBillingSectionVisibility() {

        if (!billingSection) {
            return;
        }

        if (
            sameBillingCheckbox &&
            sameBillingCheckbox.checked
        ) {

            billingSection.style.display =
                "none";

        } else {

            billingSection.style.display =
                "block";
        }
    }


    // =========================================================
    // PAYMENT METHOD
    // =========================================================

    function getSelectedPaymentMethod() {

        const selected =
            paymentMethodRadios.find(
                function (radio) {
                    return radio.checked;
                }
            );

        return selected
            ? selected.value
            : null;
    }


    function isNewCardSelected() {

        return (
            getSelectedPaymentMethod()
            ===
            "new_card"
        );
    }


    function isStoredCardSelected() {

        return (
            getSelectedPaymentMethod()
            ===
            "stored_card"
        );
    }


    function getSelectedStoredCardId() {

        const selected =
            storedCardRadios.find(
                function (radio) {
                    return radio.checked;
                }
            );

        return selected
            ? selected.value
            : null;
    }


    function isStoredCardSelectionComplete() {

        return (
            isStoredCardSelected() &&
            Boolean(
                getSelectedStoredCardId()
            )
        );
    }


    function updateCardFormVisibility() {

        if (newCardForm) {

            newCardForm.hidden =
                !isNewCardSelected();
        }


        if (storedCardForm) {

            storedCardForm.hidden =
                !isStoredCardSelected();
        }
    }


    // =========================================================
    // STORED CARD
    // =========================================================

    storedCardRadios.forEach(
        function (radio) {

            radio.addEventListener(
                "change",
                function () {

                    updatePaymentButtonState();

                }
            );

        }
    );


    // =========================================================
    // CARD
    // =========================================================

    function getNormalizedCardNumber() {

        return String(
            cardNumberInput
                ? cardNumberInput.value
                : ""
        )
            .replace(
                /\s+/g,
                ""
            );
    }


    function isAmericanExpressCard() {

        const cardNumber =
            getNormalizedCardNumber();

        return /^3[47]/.test(
            cardNumber
        );
    }


    function getExpectedCvcLength() {

        return isAmericanExpressCard()
            ? 4
            : 3;
    }


    // =========================================================
    // CVC VALIDATION MESSAGE
    // =========================================================

    function updateCardCvcValidationMessage() {

        if (
            !cardCvcInput ||
            !cardCvcError
        ) {
            return;
        }

        /*
         * Sadece yeni kart seçiliyken
         * CVC validation göster.
         */
        if (!isNewCardSelected()) {

            cardCvcError.textContent = "";
            cardCvcError.hidden = true;

            cardCvcInput.removeAttribute(
                "aria-invalid"
            );

            return;
        }


        const cvc =
            String(
                cardCvcInput.value || ""
            ).trim();


        /*
         * Henüz CVC girilmeye başladıysa
         * erken hata göstermiyoruz.
         */
        if (cvc.length < 3) {

            cardCvcError.textContent = "";
            cardCvcError.hidden = true;

            cardCvcInput.removeAttribute(
                "aria-invalid"
            );

            return;
        }


        const isAmex =
            isAmericanExpressCard();

        const expectedLength =
            isAmex
                ? 4
                : 3;


        if (
            cvc.length !==
            expectedLength
        ) {

            if (isAmex) {

                cardCvcError.textContent =
                    "American Express kartlarda CVC 4 haneli olmalıdır.";

            } else {

                cardCvcError.textContent =
                    "CVC 3 haneli olmalıdır.";
            }

            cardCvcError.hidden = false;

            cardCvcInput.setAttribute(
                "aria-invalid",
                "true"
            );

            return;
        }


        /*
         * Geçerli.
         */
        cardCvcError.textContent = "";
        cardCvcError.hidden = true;

        cardCvcInput.removeAttribute(
            "aria-invalid"
        );
    }


    // =========================================================
    // CARD FORM COMPLETE
    // =========================================================

    function isCardFormComplete() {

        if (!isNewCardSelected()) {
            return false;
        }

        const holderName =
            String(
                cardHolderNameInput
                    ? cardHolderNameInput.value
                    : ""
            ).trim();

        const cardNumber =
            getNormalizedCardNumber();

        const expireMonth =
            String(
                cardExpireMonthInput
                    ? cardExpireMonthInput.value
                    : ""
            ).trim();

        const expireYear =
            String(
                cardExpireYearInput
                    ? cardExpireYearInput.value
                    : ""
            ).trim();

        const cvc =
            String(
                cardCvcInput
                    ? cardCvcInput.value
                    : ""
            ).trim();

        const expectedCvcLength =
            getExpectedCvcLength();

        const cvcValid =
            new RegExp(
                `^\\d{${expectedCvcLength}}$`
            ).test(cvc);

        return (
            holderName.length >= 2 &&
            /^\d{15,16}$/.test(
                cardNumber
            ) &&
            /^\d{1,2}$/.test(
                expireMonth
            ) &&
            /^\d{2,4}$/.test(
                expireYear
            ) &&
            cvcValid
        );
    }


    // =========================================================
    // CARD FORM VALIDATION ERROR
    // =========================================================

    function getCardFormValidationError() {

        if (!isNewCardSelected()) {
            return null;
        }

        const holderName =
            String(
                cardHolderNameInput?.value || ""
            ).trim();

        const cardNumber =
            getNormalizedCardNumber();

        const expireMonth =
            String(
                cardExpireMonthInput?.value || ""
            ).trim();

        const expireYear =
            String(
                cardExpireYearInput?.value || ""
            ).trim();

        const cvc =
            String(
                cardCvcInput?.value || ""
            ).trim();


        if (holderName.length < 2) {

            return (
                "Kart üzerindeki ad soyadı kontrol edin."
            );
        }


        if (
            !/^\d{15,16}$/.test(
                cardNumber
            )
        ) {

            return (
                "Kart numarası geçersiz."
            );
        }


        if (
            !/^\d{1,2}$/.test(
                expireMonth
            )
        ) {

            return (
                "Kart son kullanma ayını kontrol edin."
            );
        }


        if (
            !/^\d{2,4}$/.test(
                expireYear
            )
        ) {

            return (
                "Kart son kullanma yılını kontrol edin."
            );
        }


        const expectedCvcLength =
            getExpectedCvcLength();


        if (
            !new RegExp(
                `^\\d{${expectedCvcLength}}$`
            ).test(cvc)
        ) {

            if (isAmericanExpressCard()) {

                return (
                    "American Express kartlarda CVC 4 haneli olmalıdır."
                );
            }

            return (
                "CVC 3 haneli olmalıdır."
            );
        }


        return null;
    }


    // =========================================================
    // PAYMENT BUTTON
    // =========================================================

    function updatePaymentButtonState() {

        if (!paymentButton) {
            return;
        }


        const shippingSelected =
            Boolean(
                getSelectedShippingAddressId()
            );


        let billingValid = false;


        if (
            sameBillingCheckbox &&
            sameBillingCheckbox.checked
        ) {

            billingValid =
                shippingSelected;

        } else {

            billingValid =
                Boolean(
                    getSelectedBillingAddressId()
                );
        }


        const paymentMethod =
            getSelectedPaymentMethod();


        const paymentSelected =
            Boolean(
                paymentMethod
            );


        let paymentValid = false;


        if (
            paymentMethod ===
            "new_card"
        ) {

            paymentValid =
                isCardFormComplete();

        } else if (
            paymentMethod ===
            "stored_card"
        ) {

            paymentValid =
                isStoredCardSelectionComplete();
        }


        paymentButton.disabled = !(
            shippingSelected &&
            billingValid &&
            paymentSelected &&
            paymentValid &&
            !isPaymentProcessing
        );
    }


    // =========================================================
    // PAYMENT ERROR
    // =========================================================

    function clearPaymentError() {

        /*
         * Artık sayfa içerisinde ayrı bir
         * hata kutusu kullanılmıyor.
         *
         * Hatalar WizardUI toast üzerinden
         * gösteriliyor.
         */

    }


    function showPaymentError(message) {

        const finalMessage =
            message ||
            "Ödeme gerçekleştirilemedi.";


        if (
            typeof WizardUI !== "undefined" &&
            typeof WizardUI.showToast === "function"
        ) {

            WizardUI.showToast(
                "error",
                finalMessage,
                6000
            );

            return;
        }


        console.error(
            "WizardUI kullanılamıyor:",
            finalMessage
        );
    }


    // =========================================================
    // API
    // =========================================================

    async function postJson(
        url,
        payload,
        idempotencyKey = null
    ) {

        const csrfToken =
            getCsrfToken();


        const headers = {
            "Content-Type":
                "application/json",

            "X-CSRFToken":
                csrfToken,

            "X-Requested-With":
                "XMLHttpRequest",
        };


        if (idempotencyKey) {

            headers["Idempotency-Key"] =
                idempotencyKey;
        }


        const response =
            await fetch(
                url,
                {
                    method: "POST",

                    credentials:
                        "same-origin",

                    headers: headers,

                    body: JSON.stringify(
                        payload
                    ),
                }
            );


        let data = null;


        try {

            data =
                await response.json();

        } catch (error) {

            throw new Error(
                "Sunucudan geçersiz bir cevap alındı."
            );
        }


        if (
            !response.ok ||
            !data.success
        ) {

            const error =
                new Error(
                    data.error ||
                    data.message ||
                    "İşlem gerçekleştirilemedi."
                );

            error.code =
                data.code || null;

            error.status =
                response.status;

            throw error;
        }


        return data;
    }


    // =========================================================
    // IDEMPOTENCY KEY
    // =========================================================

    function createIdempotencyKey() {

        if (
            typeof crypto !== "undefined" &&
            typeof crypto.randomUUID === "function"
        ) {

            return crypto.randomUUID();
        }


        return (
            Date.now().toString(36) +
            "-" +
            Math.random()
                .toString(36)
                .slice(2)
        );
    }


    // =========================================================
    // CREATE ORDER PAYLOAD
    // =========================================================

    function buildCreateOrderPayload() {

        const shippingAddressId =
            getSelectedShippingAddressId();


        if (!shippingAddressId) {

            throw new Error(
                "Lütfen teslimat adresi seçin."
            );
        }


        const payload = {

            shipping_address_id:
                shippingAddressId,

            currency:
                "TRY",
        };


        if (
            sameBillingCheckbox &&
            sameBillingCheckbox.checked
        ) {

            /*
             * Billing gönderilmiyor.
             *
             * Backend:
             *
             * billing_address = shipping_address
             */

        } else {

            const billingAddressId =
                getSelectedBillingAddressId();


            if (!billingAddressId) {

                throw new Error(
                    "Lütfen fatura adresi seçin."
                );
            }


            payload.billing_address_id =
                billingAddressId;
        }


        /*
         * cart_item_ids göndermiyoruz.
         *
         * Backend seçili CartItem'ların tamamını
         * kullanıyor.
         */

        return payload;
    }


    // =========================================================
    // SAVE CARD PAYLOAD
    // =========================================================

    function buildSaveCardPayload() {

        return {

            card_holder_name:
                String(
                    cardHolderNameInput.value
                ).trim(),

            card_number:
                getNormalizedCardNumber(),

            expire_month:
                String(
                    cardExpireMonthInput.value
                ).trim(),

            expire_year:
                String(
                    cardExpireYearInput.value
                ).trim(),

            /*
             * Kullanıcı checkout'ta kart adı girmediği için
             * provider / service tarafından boş bırakıyoruz.
             *
             * CardStorageService gerekli durumda
             * kart sahibi adını alias olarak kullanabilir.
             */
            card_alias: "",

            /*
             * İlk kayıt edilen kartı burada zorla
             * default yapmıyoruz.
             *
             * Service zaten kullanıcının default kartı yoksa
             * yeni kartı default yapıyor.
             */
            make_default: false,
        };
    }


    // =========================================================
    // SAVE NEW CARD
    // =========================================================

    async function saveNewCardIfRequested() {

        /*
         * Sadece authenticated kullanıcı için
         * checkbox DOM'a geliyor.
         */
        if (!saveNewCardInput) {
            return;
        }


        if (!saveNewCardInput.checked) {
            return;
        }


        /*
         * Kart daha önce bu checkout denemesinde
         * başarıyla kaydedildiyse tekrar oluşturma.
         */
        if (savedCardForCurrentPaymentId) {
            return;
        }


        const saveCardUrl =
            paymentButton.dataset
                .saveCardUrl;


        if (!saveCardUrl) {

            throw new Error(
                "Kart kaydetme URL yapılandırması bulunamadı."
            );
        }


        if (!saveCardIdempotencyKey) {

            saveCardIdempotencyKey =
                createIdempotencyKey();
        }


        const payload =
            buildSaveCardPayload();


        const response =
            await postJson(
                saveCardUrl,
                payload,
                saveCardIdempotencyKey
            );


        const storedCard =
            response.card;


        if (
            !storedCard ||
            !storedCard.id
        ) {

            throw new Error(
                "Kaydedilen kart bilgisi alınamadı."
            );
        }


        savedCardForCurrentPaymentId =
            storedCard.id;
    }


    // =========================================================
    // PAYMENT PAYLOAD
    // =========================================================

    function buildPaymentPayload() {

        const paymentMethod =
            getSelectedPaymentMethod();


        // =====================================================
        // STORED CARD
        // =====================================================

        if (
            paymentMethod ===
            "stored_card"
        ) {

            const storedCardId =
                getSelectedStoredCardId();


            if (!storedCardId) {

                throw new Error(
                    "Lütfen kayıtlı kart seçin."
                );
            }


            return {

                payment_method:
                    "stored_card",

                stored_card_id:
                    Number(
                        storedCardId
                    ),

                installment:
                    Number(
                        installmentInput.value
                    ),
            };
        }


        // =====================================================
        // NEW CARD
        // =====================================================

        return {

            payment_method:
                "new_card",

            card_holder_name:
                String(
                    cardHolderNameInput.value
                ).trim(),

            card_number:
                getNormalizedCardNumber(),

            expire_month:
                String(
                    cardExpireMonthInput.value
                ).trim(),

            expire_year:
                String(
                    cardExpireYearInput.value
                ).trim(),

            cvc:
                String(
                    cardCvcInput.value
                ).trim(),

            installment:
                Number(
                    installmentInput.value
                ),
        };
    }


    // =========================================================
    // PAYMENT URL
    // =========================================================

    function buildPaymentUrl(
        orderNumber
    ) {

        const template =
            paymentButton.dataset
                .paymentUrlTemplate;


        if (!template) {

            throw new Error(
                "Ödeme URL yapılandırması bulunamadı."
            );
        }


        return template.replace(
            "__ORDER_NUMBER__",
            encodeURIComponent(
                orderNumber
            )
        );
    }


    // =========================================================
    // 3DS
    // =========================================================

    function submitThreeDSHtml(
        html
    ) {

        if (
            typeof html !== "string" ||
            !html.trim()
        ) {

            throw new Error(
                "3DS ödeme içeriği alınamadı."
            );
        }


        const parser =
            new DOMParser();


        const documentFragment =
            parser.parseFromString(
                html,
                "text/html"
            );


        const sourceForm =
            documentFragment.querySelector(
                "form"
            );


        if (!sourceForm) {

            throw new Error(
                "3DS ödeme formu alınamadı."
            );
        }


        /*
         * iyzico'nun döndürdüğü HTML içerisinde
         * esas olarak POST edilecek form ve hidden
         * alanlar bulunur.
         *
         * Biz formu mevcut document'e taşıyıp
         * doğrudan submit ediyoruz.
         */

        const form =
            document.createElement(
                "form"
            );


        form.method =
            sourceForm.getAttribute(
                "method"
            ) || "POST";


        form.action =
            sourceForm.getAttribute(
                "action"
            ) || "";


        form.target = "_self";


        form.style.display =
            "none";


        Array.from(
            sourceForm.attributes
        ).forEach(
            function (attribute) {

                if (
                    attribute.name ===
                    "method" ||
                    attribute.name ===
                    "action" ||
                    attribute.name ===
                    "target"
                ) {
                    return;
                }


                form.setAttribute(
                    attribute.name,
                    attribute.value
                );
            }
        );


        Array.from(
            sourceForm.elements
        ).forEach(
            function (element) {

                if (
                    element.name
                ) {

                    const input =
                        document.createElement(
                            "input"
                        );


                    input.type =
                        "hidden";


                    input.name =
                        element.name;


                    input.value =
                        element.value;


                    form.appendChild(
                        input
                    );
                }
            }
        );


        document.body.appendChild(
            form
        );


        /*
         * Submit.
         *
         * Bundan sonra browser iyzico'nun
         * 3DS sayfasına gider.
         */

        form.submit();
    }


    // =========================================================
    // CHECKOUT PAYMENT FLOW
    // =========================================================

    async function startPayment() {

        if (isPaymentProcessing) {
            return;
        }


        clearPaymentError();


        // -----------------------------------------------------
        // FRONTEND VALIDATION
        // -----------------------------------------------------

        if (
            !getSelectedShippingAddressId()
        ) {

            showPaymentError(
                "Lütfen teslimat adresi seçin."
            );

            return;
        }


        if (
            !sameBillingCheckbox?.checked &&
            !getSelectedBillingAddressId()
        ) {

            showPaymentError(
                "Lütfen fatura adresi seçin."
            );

            return;
        }


        const paymentMethod =
            getSelectedPaymentMethod();


        if (!paymentMethod) {

            showPaymentError(
                "Lütfen ödeme yöntemi seçin."
            );

            return;
        }


        if (
            paymentMethod ===
            "new_card"
        ) {

            const cardValidationError =
                getCardFormValidationError();


            if (cardValidationError) {

                showPaymentError(
                    cardValidationError
                );

                return;
            }

        } else if (
            paymentMethod ===
            "stored_card"
        ) {

            if (
                !isStoredCardSelectionComplete()
            ) {

                showPaymentError(
                    "Lütfen kayıtlı kart seçin."
                );

                return;
            }

        } else {

            showPaymentError(
                "Geçersiz ödeme yöntemi."
            );

            return;
        }


        isPaymentProcessing = true;

        paymentButton.disabled = true;


        const originalButtonHtml =
            paymentButton.innerHTML;


        paymentButton.innerHTML = `
            <span>
                Ödeme başlatılıyor...
            </span>
            <i class="fa-solid fa-spinner fa-spin"></i>
        `;


        try {

            // =================================================
            // 1. ORDER
            // =================================================

            let orderNumber =
                currentOrderNumber;


            if (!orderNumber) {

                const createOrderUrl =
                    paymentButton.dataset
                        .createOrderUrl;


                if (!createOrderUrl) {

                    throw new Error(
                        "Sipariş oluşturma URL yapılandırması bulunamadı."
                    );
                }


                const createOrderPayload =
                    buildCreateOrderPayload();


                const orderResponse =
                    await postJson(
                        createOrderUrl,
                        createOrderPayload
                    );


                orderNumber =
                    orderResponse.order_number;


                if (!orderNumber) {

                    throw new Error(
                        "Sipariş numarası alınamadı."
                    );
                }


                currentOrderNumber =
                    orderNumber;
            }


            // =================================================
            // 2. SAVE CARD
            // =================================================

            if (
                isNewCardSelected() &&
                saveNewCardInput &&
                saveNewCardInput.checked
            ) {

                paymentButton.innerHTML = `
                    <span>
                        Kartınız kaydediliyor...
                    </span>
                    <i class="fa-solid fa-spinner fa-spin"></i>
                `;


                await saveNewCardIfRequested();
            }


            // =================================================
            // 3. PAYMENT INITIALIZE
            // =================================================

            paymentButton.innerHTML = `
                <span>
                    Güvenli ödeme ekranı açılıyor...
                </span>
                <i class="fa-solid fa-spinner fa-spin"></i>
            `;


            const paymentUrl =
                buildPaymentUrl(
                    orderNumber
                );


            const paymentPayload =
                buildPaymentPayload();


            const paymentResponse =
                await postJson(
                    paymentUrl,
                    paymentPayload
                );


            // =================================================
            // 4. 3DS
            // =================================================

            if (
                !paymentResponse
                    .three_ds_html_content
            ) {

                throw new Error(
                    "3DS ödeme içeriği alınamadı."
                );
            }


            /*
             * Artık kullanıcı iyzico 3DS sayfasına
             * yönlendirilecek.
             */

            submitThreeDSHtml(
                paymentResponse
                    .three_ds_html_content
            );


        } catch (error) {

            console.error(
                "Checkout payment error:",
                error
            );


            isPaymentProcessing =
                false;


            paymentButton.innerHTML =
                originalButtonHtml;


            showPaymentError(
                error.message ||
                "Ödeme başlatılamadı. Lütfen tekrar deneyin."
            );


            updatePaymentButtonState();
        }
    }


    // =========================================================
    // SHIPPING ADDRESS CHANGE
    // =========================================================

    shippingRadios.forEach(
        function (radio) {

            radio.addEventListener(
                "change",
                function () {

                    /*
                     * Checkbox açıksa:
                     *
                     * Teslimat = Fatura
                     */
                    if (
                        sameBillingCheckbox &&
                        sameBillingCheckbox.checked
                    ) {

                        syncBillingWithShipping();
                    }


                    updateAddressVisualState();
                    updatePaymentButtonState();
                }
            );

        }
    );


    // =========================================================
    // BILLING ADDRESS CHANGE
    // =========================================================

    billingRadios.forEach(
        function (radio) {

            radio.addEventListener(
                "change",
                function () {

                    /*
                     * Sadece bağımsız fatura seçimi sırasında
                     * yeni değeri kaydet.
                     */
                    if (
                        sameBillingCheckbox &&
                        !sameBillingCheckbox.checked
                    ) {

                        previousBillingAddressId =
                            this.value;
                    }


                    updateAddressVisualState();
                    updatePaymentButtonState();
                }
            );

        }
    );


    // =========================================================
    // SAME BILLING CHECKBOX
    // =========================================================

    if (sameBillingCheckbox) {

        sameBillingCheckbox.addEventListener(
            "change",
            function () {

                if (this.checked) {

                    /*
                     * Checkbox açılmadan önceki bağımsız
                     * fatura adresini sakla.
                     */
                    const currentBilling =
                        getSelectedBillingAddressId();


                    if (currentBilling) {

                        previousBillingAddressId =
                            currentBilling;
                    }


                    /*
                     * Fatura = teslimat
                     */
                    syncBillingWithShipping();

                } else {

                    /*
                     * Checkbox kapanınca daha önceki
                     * bağımsız fatura adresini geri getir.
                     */
                    if (
                        previousBillingAddressId
                    ) {

                        selectBillingAddress(
                            previousBillingAddressId
                        );
                    }
                }


                updateBillingSectionVisibility();
                updateAddressVisualState();
                updatePaymentButtonState();
            }
        );
    }


    // =========================================================
    // PAYMENT METHOD
    // =========================================================

    paymentMethodRadios.forEach(
        function (radio) {

            radio.addEventListener(
                "change",
                function () {

                    clearPaymentError();

                    updateCardFormVisibility();

                    updateCardCvcValidationMessage();

                    updatePaymentButtonState();
                }
            );

        }
    );


    // =========================================================
    // CARD INPUT EVENTS
    // =========================================================

    /*
     * Kart sahibi adı:
     * Harf ve boşluklar korunur.
     */
    if (cardHolderNameInput) {

        cardHolderNameInput.addEventListener(
            "input",
            function () {

                clearPaymentError();

                updatePaymentButtonState();
            }
        );
    }


    /*
     * Son kullanma ayı:
     * Sadece rakam.
     */
    if (cardExpireMonthInput) {

        cardExpireMonthInput.addEventListener(
            "input",
            function () {

                this.value =
                    this.value
                        .replace(
                            /\D/g,
                            ""
                        )
                        .slice(
                            0,
                            2
                        );

                clearPaymentError();

                updatePaymentButtonState();
            }
        );
    }


    /*
     * Son kullanma yılı:
     * Sadece rakam.
     */
    if (cardExpireYearInput) {

        cardExpireYearInput.addEventListener(
            "input",
            function () {

                this.value =
                    this.value
                        .replace(
                            /\D/g,
                            ""
                        )
                        .slice(
                            0,
                            4
                        );

                clearPaymentError();

                updatePaymentButtonState();
            }
        );
    }


    /*
     * CVC:
     * Sadece rakam.
     *
     * Ayrıca her input'ta:
     * - AmEx / diğer kart ayrımı yapılır
     * - validation mesajı güncellenir
     * - ödeme butonu güncellenir
     */
    if (cardCvcInput) {

        cardCvcInput.addEventListener(
            "input",
            function () {

                this.value =
                    this.value
                        .replace(
                            /\D/g,
                            ""
                        )
                        .slice(
                            0,
                            4
                        );

                clearPaymentError();

                updateCardCvcValidationMessage();

                updatePaymentButtonState();
            }
        );
    }


    // =========================================================
    // CARD NUMBER FORMATTING
    // =========================================================

    if (cardNumberInput) {

        cardNumberInput.addEventListener(
            "input",
            function () {

                let value =
                    this.value
                        .replace(
                            /\D/g,
                            ""
                        )
                        .slice(
                            0,
                            16
                        );


                const groups =
                    value.match(
                        /.{1,4}/g
                    );


                this.value =
                    groups
                        ? groups.join(" ")
                        : "";


                /*
                 * Kart tipi değişmiş olabilir.
                 *
                 * Örneğin:
                 * Visa -> AmEx
                 * AmEx -> Visa
                 *
                 * Bu yüzden CVC validation yeniden çalıştırılır.
                 */
                updateCardCvcValidationMessage();

                clearPaymentError();

                updatePaymentButtonState();
            }
        );
    }


    // =========================================================
    // PAYMENT BUTTON
    // =========================================================

    if (paymentButton) {

        paymentButton.addEventListener(
            "click",
            function () {

                startPayment();

            }
        );
    }


    // =========================================================
    // ADDRESS EDIT
    // =========================================================

    const addressEditButtons =
        document.querySelectorAll(
            ".btn-address-edit"
        );


    addressEditButtons.forEach(
        function (button) {

            button.addEventListener(
                "click",
                function (event) {

                    event.preventDefault();
                    event.stopPropagation();


                    const addressId =
                        this.dataset.id;


                    console.debug(
                        "Adres düzenleme:",
                        addressId
                    );
                }
            );

        }
    );


    // =========================================================
    // SHIPPING COUNTDOWN
    // =========================================================

    function initShippingCountdown() {

        const timerElements =
            document.querySelectorAll(
                ".js-shipping-timer-text"
            );


        if (
            timerElements.length === 0
        ) {
            return;
        }


        function updateTimer() {

            const now = new Date();


            /*
             * Türkiye saati.
             *
             * Türkiye UTC+3 kullandığı için
             * cart.js'deki mevcut mantıkla
             * aynı davranışı sürdürüyoruz.
             */
            const utc =
                now.getTime() +
                (
                    now.getTimezoneOffset() *
                    60000
                );


            const trTime =
                new Date(
                    utc +
                    (3600000 * 3)
                );


            let target =
                new Date(trTime);


            target.setHours(
                21,
                0,
                0,
                0
            );


            if (
                trTime >= target
            ) {

                target.setDate(
                    target.getDate() + 1
                );
            }


            const diffMs =
                target - trTime;


            const diffHours =
                Math.floor(
                    (
                        diffMs %
                        86400000
                    ) /
                    3600000
                );


            const diffMinutes =
                Math.floor(
                    (
                        diffMs %
                        3600000
                    ) /
                    60000
                );


            const text =
                `
                <i class="fa-regular fa-clock"></i>
                ${diffHours} saat ${diffMinutes} dk
                içinde sipariş verirsen yarın kargoda!
                `;


            timerElements.forEach(
                function (element) {

                    element.innerHTML =
                        text;
                }
            );
        }


        updateTimer();


        setInterval(
            updateTimer,
            60000
        );
    }


    // =========================================================
    // INITIAL STATE
    // =========================================================

    /*
     * Sayfa açılırken mevcut billing seçimini
     * hafızaya al.
     */
    const initialBillingAddress =
        getSelectedBillingAddressId();


    if (initialBillingAddress) {

        previousBillingAddressId =
            initialBillingAddress;
    }


    /*
     * Checkbox başlangıçta açıksa:
     *
     * billing = shipping
     */
    if (
        sameBillingCheckbox &&
        sameBillingCheckbox.checked
    ) {

        syncBillingWithShipping();
    }


    updateBillingSectionVisibility();
    updateAddressVisualState();
    updateCardFormVisibility();
    updateCardCvcValidationMessage();
    updatePaymentButtonState();

    initPriceFormatting();
    initShippingCountdown();
});