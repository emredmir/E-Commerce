document.addEventListener("DOMContentLoaded", function() {

    // --- AYARLAR ---
    const FREE_SHIPPING_LIMIT = 750; // Kargo bedava limitini buradan ayarlayabilirsin (TL)
    const SHIPPING_FEE = 99.99;

    // 1. ÖNCEKİ YARDIMCI FONKSİYONLAR (Para Formatı ve Kargo Sayacı)

    // Gelen veri "4,00", "4.00", "1.234,56" veya number olabilir. Hepsini standart JS sayısına çevirir.
    const parsePriceToFloat = (val) => {
        if (typeof val === 'number') return val;
        if (val === null || val === undefined || val === '') return 0;

        // "TL" yazısını ve boşlukları temizle
        let str = String(val).replace(/TL/g, '').trim();

        // Eğer hem nokta hem virgül varsa (örn: 1.234,56)
        if (str.includes('.') && str.includes(',')) {
            str = str.replace(/\./g, ''); // binlik noktasını sil
            str = str.replace(',', '.');  // virgülü ondalık noktası yap
        }
        // Eğer sadece virgül varsa (örn: 4,00)
        else if (str.includes(',')) {
            str = str.replace(',', '.');
        }
        // Eğer sadece nokta varsa (4.00) veya zaten düz sayıysa hiçbir replace işlemine gerek yok.

        return parseFloat(str) || 0;
    };

    const formatMoneyTR = (val) => {
        const num = parsePriceToFloat(val);
        
        return new Intl.NumberFormat('tr-TR', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(num);
    };


    function initPriceFormatting() {
        document.querySelectorAll('.js-format-money, .cart-current-price, .cart-old-price').forEach(el => {
            const raw = el.getAttribute('data-raw') || el.innerText.replace('TL', '').trim();
            el.setAttribute('data-raw', raw); 
            el.innerText = formatMoneyTR(raw) + (el.id === 'summary-total-price' ? ' TL' : el.classList.contains('js-format-money') ? '' : ' TL');
        });
        // Mağaza bazlı subtotal + shipping hesaplanır.
        const storeTotals = updateStoreTotals();

        // Kargo sayacını ilk kez çalıştırıyoruz. subtotal + mağaza bazlı shipping toplamı.
        updateShippingProgress(storeTotals);
    }

    function updateShippingProgress(storeTotals) {
        const feeDisplay =
            document.getElementById('shipping-fee-display');

        const progressText =
            document.getElementById('shipping-progress-text');

        const barEl =
            document.getElementById('shipping-bar');

        const finalPriceEl =
            document.getElementById('summary-final-price');

        if (!feeDisplay) {
            return;
        }

        /*
         * ---------------------------------------------------------------
         * STORE TOTALS
         * ---------------------------------------------------------------
         *
         * Beklenen yapı:
         *
         * [
         *     {
         *         storeId: 1,
         *         subtotal: 850.00,
         *         shipping: 0.00,
         *         total: 850.00
         *     },
         *     {
         *         storeId: 2,
         *         subtotal: 100.00,
         *         shipping: 99.99,
         *         total: 199.99
         *     }
         * ]
         */

        if (!Array.isArray(storeTotals)) {
            storeTotals = [];
        }

        let subtotal = 0;
        let shippingTotal = 0;

        storeTotals.forEach(store => {
            subtotal += Number(store.subtotal) || 0;
            shippingTotal += Number(store.shipping) || 0;
        });

        subtotal = Number(subtotal.toFixed(2));
        shippingTotal = Number(shippingTotal.toFixed(2));

        /*
         * ---------------------------------------------------------------
         * EMPTY CART / NO SELECTED ITEMS
         * ---------------------------------------------------------------
         */

        if (subtotal <= 0) {
            feeDisplay.innerHTML = '0,00 TL';

            if (progressText) {
                progressText.innerHTML =
                    'Ödeme yapmak için ürün seçin.';
            }

            if (barEl) {
                barEl.style.width = '0%';
                barEl.classList.remove('success');
            }

            if (finalPriceEl) {
                finalPriceEl.innerHTML =
                    '<span class="js-format-money" data-raw="0">0,00</span> TL';
            }

            return;
        }

        /*
         * ---------------------------------------------------------------
         * SHIPPING DISPLAY
         * ---------------------------------------------------------------
         *
         * Buradaki shippingTotal:
         *
         * Store A shipping
         * + Store B shipping
         * + Store C shipping
         *
         * toplamıdır.
         */

        if (shippingTotal <= 0) {
            feeDisplay.innerHTML = `
                <span style="color: var(--green-600); font-weight:700;">
                    <i class="fa-solid fa-truck-fast"></i>
                    Bedava
                </span>
            `;
        } else {
            feeDisplay.innerHTML =
                `${formatMoneyTR(shippingTotal)} TL`;
        }

        /*
         * ---------------------------------------------------------------
         * PROGRESS MESSAGE
         * ---------------------------------------------------------------
         *
         * Artık tek bir global "Bedava kargoya X TL kaldı"
         * göstermek doğru değil.
         *
         * Çünkü ücretsiz kargo Store bazında hesaplanıyor.
         */

        if (progressText) {

            const paidShippingStores =
                storeTotals.filter(
                    store => store.shipping > 0
                );
            
            const freeShippingStores =
                storeTotals.filter(
                    store =>
                        store.subtotal >= FREE_SHIPPING_LIMIT
                );
            
            /*
             * Bütün aktif mağazalarda kargo bedavaysa
             */
            if (
                freeShippingStores.length > 0
                && paidShippingStores.length === 0
            ) {
            
                progressText.innerHTML = `
                    <span class="success-msg">
                        <i class="fa-solid fa-gift"></i>
                        Tüm mağazalarda kargo bedava!
                    </span>
                `;
            
                if (barEl) {
                    barEl.style.width = '100%';
                    barEl.classList.add('success');
                }
            
            } else {
            
                /*
                 * Tek global progress artık yanıltıcı olacağı için
                 * sadece mağaza bazlı hesaplama yapıldığını belirtiyoruz.
                 */
                progressText.innerHTML =
                    'Kargo ücretleri mağaza bazında hesaplanır.';
            
                if (barEl) {
                    barEl.style.width = '0%';
                    barEl.classList.remove('success');
                }
            }
        }

        /*
         * ---------------------------------------------------------------
         * FINAL TOTAL
         * ---------------------------------------------------------------
         *
         * Order toplamı:
         *
         * subtotal + toplam mağaza kargoları
         */

        const finalTotal =
            Number(
                (
                    subtotal + shippingTotal
                ).toFixed(2)
            );

        if (finalPriceEl) {
            finalPriceEl.innerHTML = `
                <span
                    class="js-format-money"
                    data-raw="${finalTotal}"
                >
                    ${formatMoneyTR(finalTotal)}
                </span>
                TL
            `;
        }
    }

    // Başlangıçta formatla
    initPriceFormatting();


    // 2. CSRF TOKEN ALICI
    function getCSRFToken() {
        return document.querySelector('[name=csrfmiddlewaretoken]').value;
    }


    // 3. API İSTEK YÖNETİCİSİ (CART API)
    const CartAPI = {
        async request(endpoint, method = 'POST', body = null) {
            const headers = {
                'X-CSRFToken': getCSRFToken(),
                'Content-Type': 'application/json'
            };
            const options = { method, headers };
            if (body) options.body = JSON.stringify(body);
            
            try {
                const response = await fetch(`/cart/api/${endpoint}/`, options);
                return await response.json();
            } catch (error) {
                console.error("API Error:", error);
                return { success: false, error: "Sunucu ile iletişim kurulamadı." };
            }
        }
    };


    // 4. UI GÜNCELLEYİCİ (Sağ taraftaki sipariş özetini günceller)
    function updateCartTotalsUI(data) {
        document.getElementById('header-total-items').textContent = `(${data.total_items} Ürün)`;
        document.getElementById('summary-selected-count').textContent = data.selected_items_count;
        
        const formattedPrice = formatMoneyTR(data.total_price) + ' TL';
        const summaryTotalEl = document.getElementById('summary-total-price');
        summaryTotalEl.textContent = formattedPrice;
        summaryTotalEl.setAttribute('data-raw', data.total_price);

        const btnCheckout = document.getElementById('btn-checkout');
        if (btnCheckout) {
            btnCheckout.disabled = (data.selected_items_count === 0);
        }

        // Toplam fiyatlar güncellendikten sonra mağaza ara toplamlarını da yeniden hesapla!
        const storeTotals = updateStoreTotals();

        // Mağaza bazlı kargo toplamını güncelle
        updateShippingProgress(storeTotals);
    }

    // MAĞAZA BAZLI ARA TOPLAM HESAPLAYICI
    function updateStoreTotals() {
        const storeTotals = [];

        document
            .querySelectorAll('.cart-store-card')
            .forEach(card => {

                const storeId = card.dataset.storeId;

                let storeSubtotal = 0;
                let storeItemCount = 0;

                card
                    .querySelectorAll('.cart-item-row')
                    .forEach(row => {

                        const checkbox =
                            row.querySelector('.js-item-checkbox');

                        /*
                         * Yalnızca:
                         *
                         * - seçili
                         * - kullanılabilir
                         *
                         * ürünler hesaplamaya girer.
                         */
                        if (
                            checkbox
                            && checkbox.checked
                            && !checkbox.disabled
                        ) {
                            const qtyInput =
                                row.querySelector('.js-qty-input');

                            const quantity =
                                parseInt(qtyInput?.value, 10) || 0;

                            const rawPrice =
                                row.getAttribute('data-unit-price') || '0';

                            const unitPrice =
                                parsePriceToFloat(rawPrice);

                            storeSubtotal +=
                                quantity * unitPrice;

                            storeItemCount += quantity;
                        }
                    });

                /*
                 * Para değerini JS floating point hatalarından
                 * mümkün olduğunca uzak tutuyoruz.
                 */
                storeSubtotal =
                    Number(storeSubtotal.toFixed(2));

                /*
                 * ----------------------------------------------------------
                 * STORE SHIPPING
                 * ----------------------------------------------------------
                 *
                 * Backend ShippingService ile aynı kurallar:
                 *
                 * subtotal <= 0
                 *      => 0
                 *
                 * subtotal >= 750
                 *      => 0
                 *
                 * subtotal < 750
                 *      => 99.99
                 */
                let shipping = 0;

                if (storeSubtotal > 0) {
                    if (storeSubtotal < FREE_SHIPPING_LIMIT) {
                        shipping = SHIPPING_FEE;
                    }
                }

                shipping =
                    Number(shipping.toFixed(2));

                const storeTotal =
                    Number(
                        (
                            storeSubtotal + shipping
                        ).toFixed(2)
                    );

                /*
                 * ----------------------------------------------------------
                 * STORE HEADER UI
                 * ----------------------------------------------------------
                 */

                const countEl =
                    card.querySelector(
                        '.js-store-item-count'
                    );

                if (countEl) {
                    countEl.textContent =
                        storeItemCount;
                }

                const priceEl =
                    card.querySelector(
                        '.js-store-price-display'
                    );

                if (priceEl) {
                    priceEl.textContent =
                        `${formatMoneyTR(storeSubtotal)} TL`;
                }

                /*
                 * ----------------------------------------------------------
                 * STORE SHIPPING UI
                 * ----------------------------------------------------------
                 */

                const shippingEl =
                    card.querySelector(
                        '.js-store-shipping-display'
                    );

                if (shippingEl) {

                    if (storeSubtotal <= 0) {

                        shippingEl.innerHTML =
                            'Kargo: —';

                    } else if (shipping <= 0) {

                        shippingEl.innerHTML = `
                            <span
                                style="
                                    color:var(--green-600);
                                    font-weight:700;
                                "
                            >
                                <i class="fa-solid fa-truck-fast"></i>
                                Kargo: Bedava
                            </span>
                        `;

                    } else {

                        shippingEl.innerHTML = `
                            Kargo:
                            <span
                                style="
                                    color:var(--teal-700);
                                    font-weight:700;
                                "
                            >
                                ${formatMoneyTR(shipping)} TL
                            </span>
                        `;
                    }
                }

                /*
                 * ----------------------------------------------------------
                 * RETURN DATA
                 * ----------------------------------------------------------
                 */

                storeTotals.push({
                    storeId: storeId,
                    subtotal: storeSubtotal,
                    shipping: shipping,
                    total: storeTotal,
                });
            });

        return storeTotals;
    }


    // 5. MİKTAR ARTIRMA / AZALTMA KONTROLLERİ
    document.querySelectorAll('.js-qty-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const action = btn.dataset.action;
            const itemId = btn.dataset.itemId;
            const input = document.querySelector(`.js-qty-input[data-item-id="${itemId}"]`);
            
            let currentVal = parseInt(input.value) || 1;
            const maxStock = parseInt(input.dataset.maxStock) || 99;

            let newVal = action === 'increase' ? currentVal + 1 : currentVal - 1;
            
            // Limit kontrolleri
            if (newVal < 1) newVal = 1;
            if (newVal > maxStock) {
                WizardUI.showToast('warning', `Bu üründen en fazla ${maxStock} adet alabilirsiniz.`);
                return;
            }
            if (newVal === currentVal) return;

            // UI'ı anında güncelle (Optimistic UI) ve butonları kısa süreliğine kilitle
            input.value = newVal;
            const wrapper = btn.closest('.cart-qty-control');
            wrapper.style.opacity = '0.5';
            wrapper.style.pointerEvents = 'none';
            
            // API İsteği
            const res = await CartAPI.request(`update/${itemId}`, 'POST', { quantity: newVal });
            
            wrapper.style.opacity = '1';
            wrapper.style.pointerEvents = 'auto';

            if (res.success) {
                updateCartTotalsUI(res);
                WizardUI.showToast('success', 'Sepetiniz güncellendi.');
            } else {
                input.value = currentVal; // Hata varsa eski değere dön
                WizardUI.showToast('error', res.error);
            }
        });
    });


    // 6. ÜRÜN SEÇİM (CHECKBOX) KONTROLLERİ
    document.querySelectorAll('.js-item-checkbox').forEach(chk => {
        chk.addEventListener('change', async (e) => {
            const itemId = chk.dataset.itemId;
            const isSelected = chk.checked;
            
            chk.disabled = true;
            const res = await CartAPI.request(`toggle-selection/${itemId}`, 'POST', { is_selected: isSelected });
            chk.disabled = false;

            if(res.success) {
                updateCartTotalsUI(res);
                // Seçildi mi çıkarıldı mı kontrolüne göre dinamik mesaj:
                const msg = isSelected ? 'Ürün ödeme tutarına eklendi.' : 'Ürün ödeme tutarından çıkarıldı.';
                WizardUI.showToast('success', msg);
            } else {
                chk.checked = !isSelected; // Geri al
                WizardUI.showToast('error', res.error);
            }
        });
    });


    // 7. SİLME İŞLEMİ
    document.querySelectorAll('.js-btn-delete').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            const itemId = btn.dataset.itemId;
            
            const confirmed = await WizardUI.showConfirm({
                title: 'Ürünü Sil',
                message: 'Bu ürünü sepetinizden çıkarmak istediğinize emin misiniz?',
                confirmText: 'Sil',
                cancelText: 'Vazgeç'
            });
            
            if(!confirmed) return;

            WizardUI.setButtonLoading(btn, true, '');
            const res = await CartAPI.request(`remove/${itemId}`, 'DELETE');
            
            if(res.success) {
                // DOM'dan ürünü kaldır
                const row = document.querySelector(`.cart-item-row[data-item-id="${itemId}"]`);
                const storeCard = row.closest('.cart-store-card');
                row.remove();
                
                // Eğer o mağazaya ait başka ürün kalmadıysa, mağaza kartını da komple sil
                if(storeCard.querySelectorAll('.cart-item-row').length === 0) {
                    storeCard.remove();
                }
                
                WizardUI.showToast('success', 'Ürün sepetten silindi.');
                
                // Eğer sepet tamamen boşaldıysa "Boş Sepet" tasarımını görmek için sayfayı yenile
                if(res.total_items === 0) {
                    window.location.reload();
                } else {
                    updateCartTotalsUI(res);
                }
            } else {
                WizardUI.showToast('error', res.error);
                WizardUI.setButtonLoading(btn, false, '<i class="fa-regular fa-trash-can"></i>');
            }
        });
    });


    // 8. FİYAT DEĞİŞİKLİĞİ ONAY (ACKNOWLEDGE)
    const btnAck = document.querySelector('.js-btn-acknowledge-prices');
    if(btnAck) {
        btnAck.addEventListener('click', async () => {
            WizardUI.setButtonLoading(btnAck, true, 'Onaylanıyor...');
            const res = await CartAPI.request('acknowledge-price', 'POST');
            
            if(res.success) {
                // Banner'ı DOM'dan sil
                document.getElementById('price-alert-banner').remove();
                
                // Ürünlerdeki üstü çizili eski fiyatları temizle (isteğe bağlı güzellik)
                document.querySelectorAll('.cart-old-price').forEach(el => el.remove());
                WizardUI.showToast('success', 'Fiyat değişiklikleri onaylandı.');
            } else {
                WizardUI.showToast('error', res.error);
                WizardUI.setButtonLoading(btnAck, false, 'Anladım');
            }
        });
    }


    // 9. CHECKOUT KONTROLÜ (Sepeti Onayla Butonu)
    const btnCheckout = document.getElementById('btn-checkout');
    if(btnCheckout) {
        btnCheckout.addEventListener('click', async () => {
            WizardUI.setButtonLoading(btnCheckout, true, 'Kontrol Ediliyor...');
            const res = await CartAPI.request('validate-checkout', 'POST');
            
            if(res.success) {
                if(res.is_valid) {
                    // Order (Ödeme) sayfasına yönlendir.
                    window.location.href = '/orders/checkout/'; 
                } else {
                    // is_valid: false -> Stok değişmiş veya ürün satıştan kalkmış
                    // CartService arkaplanda sepeti (adeti/seçimi) güncelledi.
                    // Kullanıcıya uyarı verip sayfayı yeniliyoruz ki yeni durumu görsün.
                    WizardUI.showToast('warning', 'Sepetinizdeki bazı ürünlerin stok durumu değişti. Sepetiniz güncelleniyor...', 4000);
                    setTimeout(() => window.location.reload(), 2500);
                }
            } else {
                WizardUI.showToast('error', res.error);
                WizardUI.setButtonLoading(btnCheckout, false, 'Sepeti Onayla <i class="fa-solid fa-chevron-right"></i>');
            }
        });
    }

    // --- 10. SAAT SAYACI (21:00 YARIN KARGODA) ---
    function initShippingCountdown() {
        const timerEls = document.querySelectorAll(".js-shipping-timer-text");
        if (timerEls.length === 0) return;

        function updateTimer() {
            const now = new Date();
            const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
            const trTime = new Date(utc + (3600000 * 3)); // TR Saati

            let target = new Date(trTime);
            target.setHours(21, 0, 0, 0);

            if (trTime >= target) {
                target.setDate(target.getDate() + 1);
            }

            const diffMs = target - trTime;
            const diffHours = Math.floor((diffMs % 86400000) / 3600000);
            const diffMins = Math.floor((diffMs % 3600000) / 60000);

            const textStr = `<i class="fa-regular fa-clock"></i> ${diffHours} saat ${diffMins} dk içinde sipariş verirsen yarın kargoda!`;

            timerEls.forEach(el => {
                el.innerHTML = textStr;
            });
        }

        updateTimer();
        setInterval(updateTimer, 60000);
    }
    initShippingCountdown();

    // --- 11. SLIDER (CAROUSEL) YANA KAYDIRMA OKLARI ---
    document.querySelectorAll('.carousel-wrapper').forEach(wrapper => {
        const carousel = wrapper.querySelector('.js-carousel');
        const btnLeft = wrapper.querySelector('.js-scroll-left');
        const btnRight = wrapper.querySelector('.js-scroll-right');

        if(!carousel) return;

        // Ürün kartı genişliğine göre kaydırma miktarı (örn: 300px)
        const scrollAmount = 350; 

        btnLeft.addEventListener('click', () => {
            carousel.scrollBy({ left: -scrollAmount, behavior: 'smooth' });
        });

        btnRight.addEventListener('click', () => {
            carousel.scrollBy({ left: scrollAmount, behavior: 'smooth' });
        });
    });

});