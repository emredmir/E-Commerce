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


    // =========================================================
    // STATE
    // =========================================================

    /*
     * Checkbox tekrar kapatıldığında geri getirmek için
     * bağımsız fatura adresinin ID'sini saklıyoruz.
     */
    let previousBillingAddressId = null;


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

        const paymentSelected =
            paymentMethodRadios.some(
                function (radio) {
                    return radio.checked;
                }
            );

        paymentButton.disabled = !(
            shippingSelected &&
            billingValid &&
            paymentSelected
        );
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

                    updatePaymentButtonState();

                }
            );

        }
    );


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
    updatePaymentButtonState();

    initPriceFormatting();
    initShippingCountdown();

});