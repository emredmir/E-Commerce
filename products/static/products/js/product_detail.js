/* ==========================================================
 * MÜŞTERİ VİTRİNİ - PRODUCT DETAIL JS
 * ========================================================== */

let galleryState = {
    currentIndex: 0,
    totalImages: 0
};

document.addEventListener("DOMContentLoaded", () => {
    initGalleryState();
    initImageGallery();
    initImageZoom();
    initLightbox();
    initThumbNavigation();
    initPriceFormatting();
    initShippingCountdown();
    initTabs();
    initProductSliders();
    initInstallmentMenu();
    initFavorites();
    initQA();
});


/* ----------------------------------------------------------
 * 1. ORTAK GALERİ YÖNETİMİ
 * ---------------------------------------------------------- */
function initGalleryState() {
    const track = document.querySelector(".js-image-track");
    if (track) {
        galleryState.totalImages = track.children.length;
    }
}

// Bu fonksiyon çalışınca hem Ana sayfadaki resim hem de varsa Lightbox'taki resim değişir
function goToImage(index, isHover = false) {
    if (galleryState.totalImages === 0) return;
    
    if (index < 0) index = galleryState.totalImages - 1;
    if (index >= galleryState.totalImages) index = 0;

    galleryState.currentIndex = index;

    // 1. Ana sayfadaki büyük slider'ı kaydır
    const track = document.querySelector(".js-image-track");
    if (track) {
        track.dataset.currentIndex = index;
        track.style.transform = `translateX(-${index * 100}%)`;
    }

    // 2. Ana sayfadaki küçük resimleri (thumbnail) aktifleştir
    const mainThumbs = document.querySelectorAll(".js-thumb-item");
    mainThumbs.forEach((thumb, i) => {
        thumb.classList.toggle("active", i === index);
        // EĞER SADECE HOVER YAPILIYORSA SCROLL YAPMA (Döngüyü kırar)
        if (i === index && !isHover) {
            thumb.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        }
    });

    // 3. Lightbox slider kaydırma
    const lbTrack = document.querySelector(".js-lightbox-track");
    if (lbTrack) {
        Array.from(lbTrack.children).forEach(img => {
            img.style.transform = 'scale(1)'; // Zoomları sıfırla
        });
        lbTrack.dataset.currentIndex = index;
        lbTrack.style.transform = `translateX(-${index * 100}%)`;
    }

    // Lightbox küçük resimleri aktifleştir
    const lbThumbs = document.querySelectorAll(".js-lb-thumb");
    lbThumbs.forEach((thumb, i) => {
        thumb.classList.toggle("active", i === index);
        if (i === index && !isHover) {
            thumb.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
        }
    });
}
/* ----------------------------------------------------------
 * IMAGE GALLERY (Sliding & Hover Animation)
 * ---------------------------------------------------------- */
function initImageGallery() {
    // Küçük Resimlere Tıklama/Hover Olayı
    document.querySelectorAll(".js-thumb-item").forEach((thumb, i) => {
        thumb.addEventListener("click", () => goToImage(i, false));
        thumb.addEventListener("mouseenter", () => goToImage(i, true));
    });

    // Ana sayfa resminin yanlarındaki Oklar
    const prevBtn = document.querySelector(".js-gallery-prev");
    const nextBtn = document.querySelector(".js-gallery-next");

    if (prevBtn) prevBtn.addEventListener("click", (e) => {
        e.stopPropagation(); // Zoom kutusunu tetiklemesin
        goToImage(galleryState.currentIndex - 1, false);
    });

    if (nextBtn) nextBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        goToImage(galleryState.currentIndex + 1, false);
    });
}


/* ----------------------------------------------------------
 * 2. MAUSE TEKERLEĞİ İLE KÜÇÜK RESİM KAYDIRMA (SCROLL)
 * ---------------------------------------------------------- */
function initThumbNavigation() {
    // Oklar ile kaydırma
    document.querySelectorAll(".js-thumb-prev").forEach(btn => {
        btn.addEventListener("click", () => {
            const row = btn.parentElement.querySelector(".js-thumb-scroll");
            if (row) row.scrollBy({ left: -200, behavior: 'smooth' });
        });
    });

    document.querySelectorAll(".js-thumb-next").forEach(btn => {
        btn.addEventListener("click", () => {
            const row = btn.parentElement.querySelector(".js-thumb-scroll");
            if (row) row.scrollBy({ left: 200, behavior: 'smooth' });
        });
    });

    // Mause Tekerleği ile kaydırma (Yukarı aşağı kaydırmayı sağa sola çevirir)
    document.querySelectorAll(".js-thumb-scroll").forEach(container => {
        container.addEventListener("wheel", (e) => {
            if (container.scrollWidth > container.clientWidth) {
                e.preventDefault(); 
                container.scrollBy({ left: e.deltaY > 0 ? 150 : -150, behavior: 'auto' });
            }
        }, { passive: false });
    });
}

/* ----------------------------------------------------------
 * 3. IMAGE ZOOM
 * ---------------------------------------------------------- */
function initImageZoom() {
    const container = document.querySelector('.js-zoom-container');
    const lens = document.querySelector('.js-zoom-lens');
    const result = document.querySelector('.js-zoom-result');
    const track = document.querySelector('.js-image-track');

    if (!container || !lens || !result || !track) return;

    container.addEventListener('mouseenter', () => {
        const activeImg = track.children[galleryState.currentIndex];
        if (!activeImg || activeImg.src.includes('placeholder')) return;

        result.style.backgroundImage = `url('${activeImg.src}')`;
        lens.style.display = 'block';
        result.style.display = 'block';
    });

    container.addEventListener('mousemove', (e) => {
        const activeImg = track.children[galleryState.currentIndex];
        if (!activeImg || activeImg.src.includes('placeholder')) return;

        // Container sınırları
        const rect = container.getBoundingClientRect();
        
        let x = e.clientX - rect.left;
        let y = e.clientY - rect.top;

        // Lensin sol üst noktası (Mouse merkezde)
        let lensX = x - (lens.offsetWidth / 2);
        let lensY = y - (lens.offsetHeight / 2);

        // Lens Container Dışına Çıkmasın
        if (lensX < 0) lensX = 0;
        if (lensY < 0) lensY = 0;
        if (lensX > rect.width - lens.offsetWidth) lensX = rect.width - lens.offsetWidth;
        if (lensY > rect.height - lens.offsetHeight) lensY = rect.height - lens.offsetHeight;

        // Lensi yerleştir
        lens.style.left = lensX + 'px';
        lens.style.top = lensY + 'px';

        // Tam Yüksek Çözünürlük Eşlemesi
        const bgPosX = (lensX / (rect.width - lens.offsetWidth)) * 100;
        const bgPosY = (lensY / (rect.height - lens.offsetHeight)) * 100;
        const bgSizeRatio = (rect.width / lens.offsetWidth) * 100;

        result.style.backgroundSize = `${bgSizeRatio}%`;
        result.style.backgroundPosition = `${bgPosX}% ${bgPosY}%`;
    });

    container.addEventListener('mouseleave', () => {
        lens.style.display = 'none';
        result.style.display = 'none';
    });
}

/* ----------------------------------------------------------
 * 4. LIGHTBOX (Resme Tıklayınca Tam Ekran Açılma)
 * ---------------------------------------------------------- */
function initLightbox() {
    const container = document.querySelector('.js-zoom-container');
    const lightbox = document.querySelector('.js-lightbox');
    const closeBtn = document.querySelector('.js-lightbox-close');
    const nextBtn = document.querySelector('.js-lightbox-next');
    const prevBtn = document.querySelector('.js-lightbox-prev');
    const lbThumbs = document.querySelectorAll(".js-lb-thumb");
    const lbZoomContainer = document.querySelector(".js-lb-zoom-container");
    const lbTrack = document.querySelector(".js-lightbox-track");

    if (!container || !lightbox) return;

    // Ana resme tıklanınca Lightbox Aç
    container.addEventListener('click', (e) => {
        // Ok tuşlarına tıklandıysa lightbox'ı açma
        if(e.target.closest('.pd-main-img-nav')) return;

        goToImage(galleryState.currentIndex, false); // Resmi eşitle
        lightbox.classList.add('active');
        document.body.style.overflow = 'hidden'; 
    });

    const closeLightbox = () => {
        lightbox.classList.remove('active');
        document.body.style.overflow = '';

        // Kapatırken lightbox içindeki zoom'u sıfırla
        if(lbTrack && lbTrack.children[galleryState.currentIndex]) {
            lbTrack.children[galleryState.currentIndex].style.transition = 'transform 0.2s';
            lbTrack.children[galleryState.currentIndex].style.transform = 'scale(1)';
        }
    };

    if (closeBtn) closeBtn.addEventListener('click', closeLightbox);

    
    // Dışarıya tıklayınca kapat (İçerikteki pd-lightbox-content'e tıklanırsa kapanmaz)
    lightbox.addEventListener('click', (e) => {
        if (e.target === lightbox) closeLightbox(); 
    });

    if(nextBtn) nextBtn.addEventListener('click', () => goToImage(galleryState.currentIndex + 1, false));
    if(prevBtn) prevBtn.addEventListener('click', () => goToImage(galleryState.currentIndex - 1, false));

    // Lightbox altındaki küçük resimlere tıklama
    lbThumbs.forEach((thumb, i) => {
        thumb.addEventListener("click", () => goToImage(i, false));
    });

    // LIGHTBOX İÇİ ZOOM (Hover ile)
    if (lbZoomContainer && lbTrack) {
        lbZoomContainer.addEventListener('mousemove', (e) => {
            const activeImg = lbTrack.children[galleryState.currentIndex];
            if(!activeImg || activeImg.src.includes('placeholder')) return;
            
            // Mouse pozisyonunu hesapla (%)
            const rect = lbZoomContainer.getBoundingClientRect();
            const x = ((e.clientX - rect.left) / rect.width) * 100;
            const y = ((e.clientY - rect.top) / rect.height) * 100;
            
            // Orijin noktasını mouse'un olduğu yere çek ve 2.5 kat büyüt
            // Resmi esnetirken bulanıklaştırmaması için animasyonu kapat
            activeImg.style.transition = 'none';
            activeImg.style.transformOrigin = `${x}% ${y}%`;
            activeImg.style.transform = `scale(2.5)`;
        });
        
        lbZoomContainer.addEventListener('mouseleave', () => {
            const activeImg = lbTrack.children[galleryState.currentIndex];
            if(!activeImg) return;
            // Mouse üzerinden çekilince eski haline getir
            activeImg.style.transition = 'transform 0.3s ease-out';
            activeImg.style.transformOrigin = `center center`;
            activeImg.style.transform = `scale(1)`;
        });
    }

    // Klavye Yön Tuşları ile Navigasyon
    document.addEventListener('keydown', (e) => {
        if (!lightbox.classList.contains('active')) return;
        if (e.key === 'Escape') closeLightbox();
        if (e.key === 'ArrowRight' && nextBtn) nextBtn.click();
        if (e.key === 'ArrowLeft' && prevBtn) prevBtn.click();
    });
}

/* ----------------------------------------------------------
 * 5. PARA BİRİMİ FORMATLAMA (1500 -> 1.500,00)
 * ---------------------------------------------------------- */
function initPriceFormatting() {
    const formatMoneyTR = (val) => {
        if (!val) return val;
        let normalized = String(val).trim();
        if (normalized.includes(',') && !normalized.includes('.')) {
            normalized = normalized.replace(',', '.');
        }
        const num = parseFloat(normalized);
        if (isNaN(num)) return val;

        return new Intl.NumberFormat('tr-TR', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        }).format(num);
    };

    document.querySelectorAll('.js-format-money, .js-price-format').forEach(el => {
        const raw = el.getAttribute('data-raw') || el.innerText.trim();
        el.setAttribute('data-raw', raw); // Orjinal veriyi kaybetmemek için koru
        el.innerText = formatMoneyTR(raw);
    });
}

/* ----------------------------------------------------------
 * 6. KARGO SAYAÇ (Saat 21:00 Bazlı Yarın Kargoda)
 * ---------------------------------------------------------- */
function initShippingCountdown() {
    // Sayfadaki tüm kargo zamanı textlerini bul (Ana kutu ve Tüm Satıcılar tablosu içindekiler)
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

        const textStr = `${diffHours} saat ${diffMins} dk içinde sipariş verirsen yarın kargoda!`;

        timerEls.forEach(el => {
            el.textContent = textStr;
        });
    }

    updateTimer();
    setInterval(updateTimer, 60000);
}

/* ----------------------------------------------------------
 * 7. TABS (Ürün Açıklaması, Yorumlar, Diğer Satıcılar)
 * ---------------------------------------------------------- */
function initTabs() {
    const tabContainer = document.getElementById("product-details-tab");
    if (!tabContainer) return;

    const btns = tabContainer.querySelectorAll(".pd-tab-btn");
    const contents = tabContainer.querySelectorAll(".pd-tab-content");

    // Global bir tab açma fonksiyonu yazıyoruz ki linkler de kullanabilsin
    window.openTabByIndex = function(index) {
        if (!btns[index] || !contents[index]) return;
        
        btns.forEach(b => b.classList.remove("active"));
        contents.forEach(c => c.classList.remove("active"));

        btns[index].classList.add("active");
        contents[index].classList.add("active");
    };

    btns.forEach((btn, index) => {
        btn.addEventListener("click", () => {
            window.openTabByIndex(index);
        });
    });

    // Sayfa içindeki Değerlendirme, Yorum vb. linklere tıklanınca Tab alanına kaydır ve Tab'ı aç
    document.querySelectorAll(".js-scroll-to-tab").forEach(link => {
        link.addEventListener("click", (e) => {
            e.preventDefault();
            const targetIndex = parseInt(link.getAttribute("data-tab-index"));
            
            if (!isNaN(targetIndex)) {
                window.openTabByIndex(targetIndex);
                // Tab menüsünün olduğu yere yumuşakça kaydır
                tabContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        });
    });
}

/* ----------------------------------------------------------
 * 8. HORIZONTAL PRODUCT SLIDERS (Benzer Ürünler vs.)
 * ---------------------------------------------------------- */
function initProductSliders() {
    document.querySelectorAll(".pd-slider-container").forEach(container => {
        const track = container.querySelector(".pd-slider-track");
        const btns = container.querySelectorAll(".pd-slider-btn");
        
        if (!track || btns.length < 2) return;

        btns[0].addEventListener("click", () => {
            track.scrollBy({ left: -250, behavior: 'smooth' });
        });

        btns[1].addEventListener("click", () => {
            track.scrollBy({ left: 250, behavior: 'smooth' });
        });
    });
}

/* ----------------------------------------------------------
 * 9. TAKSİT MENÜSÜ AÇILIR/KAPANIR MANTIĞI
 * ---------------------------------------------------------- */
function initInstallmentMenu() {
    const toggleBtn = document.querySelector(".js-installment-toggle");
    const menu = document.querySelector(".js-installment-menu");


    if (!toggleBtn || !menu) return;

    // Butona tıklayınca aç/kapat
    toggleBtn.addEventListener("click", (e) => {
        e.stopPropagation(); // Tıklamanın dışarıya taşmasını engelle
        menu.classList.toggle("active");
        toggleBtn.classList.toggle("active");
        
    });

    // Menü dışında bir yere tıklanınca otomatik kapat
    document.addEventListener("click", (e) => {
        if (!menu.contains(e.target) && !toggleBtn.contains(e.target)) {
            menu.classList.remove("active");
            toggleBtn.classList.remove("active");
        }
    });
}

/* ----------------------------------------------------------
 * 10. KOLEKSİYON / FAVORİLER SİSTEMİ (AJAX & WIZARD UI)
 * ---------------------------------------------------------- */
function initFavorites() {
    const favBtn = document.querySelector(".js-fav-btn");
    const favMenu = document.querySelector(".js-fav-menu");
    const favClose = document.querySelector(".js-fav-close");
    const listsContainer = document.querySelector(".js-fav-lists");
    const createInput = document.querySelector(".js-fav-input");
    const createBtn = document.querySelector(".js-fav-submit");
    const searchInput = document.querySelector(".js-fav-search");

    if (!favBtn || !favMenu) return;

    // URL'leri ve ID'yi buton üzerinden dinamik olarak okuyoruz
    const variantId = favBtn.dataset.variantId;
    const offerId = favBtn.dataset.offerId;
    const listApiUrl = favBtn.dataset.listUrl;
    const toggleApiUrl = favBtn.dataset.toggleUrl;
    const createApiUrl = favBtn.dataset.createUrl;
    
    let isCollectionsLoaded = false; // Menü açıldığında gereksiz fetch yapmamak için

    // Django CSRF Token Alıcı
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    // SAYFA AÇILDIĞINDA SESSİZCE (SADECE ANA KALBİN DURUMUNU ÖĞRENMEK İÇİN) ÇALIŞIR
    async function initialHeartCheck() {
        try {
            const response = await fetch(`${listApiUrl}?variant_id=${variantId}`);
            if (response.ok) { 
                const data = await response.json();
                if (data.success) {
                    updateMainHeartUI(data.collections);
                }
            }
        } catch(err) { /* Sessiz hata */ }
    }
    initialHeartCheck(); // Hemen çalıştır

    // Menü Aç/Kapat (Tıklama ile)
    favBtn.addEventListener("click", async (e) => {
        e.stopPropagation();
        
        // Eğer kullanıcı giriş yapmamışsa (Django 403 döner)
        const response = await fetch(`${listApiUrl}?variant_id=${variantId}`);
        if (response.status === 403 || response.redirected) {
            
            // Bizim WizardUI modülünü kullanıyoruz
            const isConfirmed = await WizardUI.showConfirm({
                title: "Giriş Yapın",
                message: "Ürünleri favorilerinize eklemek için giriş yapmalısınız.",
                confirmText: "Giriş Yap",
                cancelText: "Kapat"
            });

            if (isConfirmed) {
                window.location.href = `/accounts/login/?next=${window.location.pathname}`;
            }
            return; // Menüyü açma
        }

        const isActive = favMenu.classList.toggle("active");
        if (isActive && !isCollectionsLoaded) {
            fetchCollections();
        }
    });

    favClose.addEventListener("click", () => favMenu.classList.remove("active"));
    document.addEventListener("click", (e) => {
        if (!favMenu.contains(e.target) && !favBtn.contains(e.target)) {
            favMenu.classList.remove("active");
        }
    });

    // 1. LİSTELERİ GETİR
    async function fetchCollections() {
        listsContainer.innerHTML = `<div style="text-align:center; padding:15px; color:var(--gray-400);"><i class="fa-solid fa-spinner fa-spin"></i></div>`;
        try {
            const response = await fetch(`${listApiUrl}?variant_id=${variantId}`);
            const data = await response.json();
            if (data.success) {
                renderCollections(data.collections);
                updateMainHeartUI(data.collections);
                isCollectionsLoaded = true; // Cache'le
            }
        } catch (err) {
            listsContainer.innerHTML = `<div style="text-align:center; padding:15px; color:var(--red-600); font-size:0.85rem;">Veriler alınamadı.</div>`;
        }
    }

    // 2. HTML RENDER
    function renderCollections(collections) {
        listsContainer.innerHTML = "";
        collections.forEach(col => {
            const item = document.createElement("div");
            item.className = `pd-fav-item ${col.is_in_list ? 'selected' : ''}`;
            item.innerHTML = `
                <div class="pd-fav-item-checkbox"><i class="fa-solid fa-check"></i></div>
                <div class="pd-fav-item-name">${col.name}</div>
            `;
            // Tıklama Olayı (Toggle)
            item.addEventListener("click", () => toggleCollection(col.id, item));
            listsContainer.appendChild(item);
        });
    }

    // LİSTELERDE CANLI ARAMA (FİLTRE) MANTIĞI
    if (searchInput) {
        searchInput.addEventListener("input", (e) => {
            const val = e.target.value.toLowerCase().trim();
            const items = listsContainer.querySelectorAll(".pd-fav-item");
            
            items.forEach(item => {
                const name = item.querySelector(".pd-fav-item-name").textContent.toLowerCase();
                // Eğer kelime geçiyorsa göster, geçmiyorsa gizle
                item.style.display = name.includes(val) ? "flex" : "none";
            });
        });
    }

    // 3. EKLE/ÇIKAR (TOGGLE) - Optimistic UI
    async function toggleCollection(collectionId, element) {
        // Kullanıcı ard arda basamasın diye UI'yi kısa süreli kilitliyoruz (Spam Koruması)
        element.style.pointerEvents = "none";
        element.style.opacity = "0.6";

        element.classList.toggle("selected");

        try {
            // DİNAMİK URL BURADA KULLANILDI
            const response = await fetch(toggleApiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({ variant_id: variantId, collection_id: collectionId, offer_id: offerId })
            });
            const data = await response.json();
            
            if (!data.success) {
                element.classList.toggle("selected"); // Hata olursa UI'yi geri al
                WizardUI.showToast("error", "İşlem gerçekleştirilemedi.");
            } else {
                checkAndUpdateMainHeart();
            }
        } catch (err) {
            element.classList.toggle("selected"); // UI'yi geri al
            WizardUI.showToast("error", "Bağlantı sorunu yaşandı.");
        } finally {
            // İşlem bitince kilidi aç
            element.style.pointerEvents = "auto";
            element.style.opacity = "1";
        }
        
    }

    // 4. YENİ LİSTE OLUŞTUR
    async function createCollection() {
        const name = createInput.value.trim();
        if (!name) return;

        createBtn.disabled = true;
        createBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i>`;

        try {
            // DİNAMİK URL BURADA KULLANILDI
            const response = await fetch(createApiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCookie('csrftoken')
                },
                body: JSON.stringify({ name: name, variant_id: variantId, offer_id: offerId })
            });
            const data = await response.json();
            
            if (data.success) {
                createInput.value = "";
                fetchCollections(); // Listeyi yenile
                WizardUI.showToast("success", "Yeni liste oluşturuldu!");
            } else {
                WizardUI.showToast("warning", data.error || "Liste oluşturulamadı.");
            }
        } catch (err) {
            WizardUI.showToast("error", "Liste oluşturulurken bir hata oluştu.");
        } finally {
            createBtn.disabled = false;
            createBtn.innerHTML = `<i class="fa-solid fa-plus"></i>`;
        }
    }

    createBtn.addEventListener("click", createCollection);
    createInput.addEventListener("keypress", (e) => {
        if (e.key === "Enter") createCollection();
    });

    // 5. ANA KALP BUTONUNUN DURUMUNU GÜNCELLE
    function updateMainHeartUI(collections) {
        // .some() metodu koleksiyonların EN AZ BİRİNDE is_in_list True ise true döner
        const isInAnyList = collections.some(c => c.is_in_list);
        
        if (isInAnyList) {
            favBtn.classList.add("is-active");
        } else {
            favBtn.classList.remove("is-active");
        }
    }

    // Toggle sonrası UI'daki ilk item'ı (Favorilerim) sayarak ana kalbi kontrol eder
    function checkAndUpdateMainHeart() {
        const allItems = listsContainer.querySelectorAll(".pd-fav-item");
        let isSelectedInAny = false;

        // Ekranda (DOM) basılı olanlardan herhangi biri 'selected' ise kırmızı yap
        allItems.forEach(item => {
            if (item.classList.contains("selected")) {
                isSelectedInAny = true;
            }
        });

        if (isSelectedInAny) {
            favBtn.classList.add("is-active");
        } else {
            favBtn.classList.remove("is-active");
        }
    }
}

/* ----------------------------------------------------------
 * 11. Soru/Cevap Sistemi (AJAX, Filtreleme ve Modal)
 * ---------------------------------------------------------- */
function initQA() {
    // ---------------------------------------------------------
    // 1. GEREKLİ HTML ELEMANLARINI SEÇME
    // ---------------------------------------------------------
    const searchInput = document.getElementById('qa-search-input');
    const topicSelect = document.getElementById('qa-topic-select');
    const storeSelect = document.getElementById('qa-store-select');
    const sortSelect = document.getElementById('qa-sort-select');

    const qaWrapper = document.getElementById('qa-ajax-wrapper');
    const qaContainer = document.getElementById('qa-list-container');
    const tabQaElement = document.getElementById('tab-qa');
    const productIdEl = document.getElementById('btn-open-ask-modal');
    
    const productId = productIdEl ? productIdEl.dataset.productId : null;

    // Eğer sayfada soru-cevap alanı yoksa kodu boşuna çalıştırma
    if (!qaWrapper || !qaContainer || !productId) return;

    // ---------------------------------------------------------
    // 2. AJAX FETCH İŞLEMLERİ (Soruları Yenileme)
    // ---------------------------------------------------------
    let qaController = null; // Üst üste hızlı tıklamaları engellemek için

    async function fetchQAList(urlParams) {
        // Eğer önceki istek bitmediyse iptal et (Performans için)
        if (qaController) qaController.abort();
        
        const controller = new AbortController();
        qaController = controller;

        const fetchParams = new URLSearchParams(urlParams);
        fetchParams.set('ajax', 'qa');

        const fetchUrl = `/products/api/qa/list/${productId}/?${fetchParams.toString()}`;

        // Yükleniyor efekti (Opaklığı düşür)
        qaWrapper.style.transition = 'opacity 0.2s ease';
        qaWrapper.style.opacity = '0.5';
        qaWrapper.style.pointerEvents = 'none';

        try {
            const response = await fetch(fetchUrl, {
                method: 'GET',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'Accept': 'application/json',
                },
                signal: controller.signal,
            });

            if (!response.ok) throw new Error(`Hata: ${response.status}`);

            const data = await response.json();

            if (!data.success) throw new Error(data.error || 'Q&A yüklenemedi.');

            // Gelen yeni HTML'i ekrana bas
            qaContainer.innerHTML = data.html;

        } catch (error) {
            if (error.name === 'AbortError') return; // İstek iptal edildiyse sessizce çık
            window.WizardUI?.showToast?.('error', 'Sorular yüklenirken bir hata oluştu.');
        } finally {
            // Sadece son istek tamamlandığında ekranı normale döndür
            if (qaController === controller) {
                qaWrapper.style.opacity = '1';
                qaWrapper.style.pointerEvents = 'auto';
                qaController = null;
            }
        }
    }

    // ---------------------------------------------------------
    // 3. PARAMETRE GÜNCELLEME VE FİLTRE EVENTLERİ
    // ---------------------------------------------------------
    function updateQAParams(key, value) {
        const urlParams = new URLSearchParams(window.location.search);

        // Sayfa numarası değişmiyorsa (yani filtre değişiyorsa) 1. sayfaya dön
        if (key !== 'qa_page') urlParams.set('qa_page', '1');

        if (value && value !== 'all') {
            urlParams.set(key, value);
        } else {
            urlParams.delete(key);
        }

        // URL'yi sayfayı yenilemeden görsel olarak güncelle
        const queryString = urlParams.toString();
        const newUrl = window.location.pathname + (queryString ? `?${queryString}` : '') + '#tab-qa';
        window.history.replaceState({ path: newUrl }, '', newUrl);

        // Yeni verilere göre AJAX at
        fetchQAList(urlParams);
    }

    // Doğru HTML ID'lerine sahip Select ve Inputlar için dinleyiciler
    if (topicSelect) {
        topicSelect.addEventListener('change', (e) => updateQAParams('qa_topic', e.target.value));
    }
    if (storeSelect) {
        storeSelect.addEventListener('change', (e) => updateQAParams('qa_store', e.target.value));
    }
    if (sortSelect) {
        sortSelect.addEventListener('change', (e) => updateQAParams('qa_sort', e.target.value));
    }
    if (searchInput) {
        searchInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                const val = e.target.value.trim();
                updateQAParams('qa_search', val === "" ? "all" : val);
            }
        });
    }

    // ---------------------------------------------------------
    // 4. SAYFA YENİLENDİĞİNDE Q&A SEKMESİNE ODAKLANMA
    // ---------------------------------------------------------
    if (window.location.hash === '#tab-qa' || window.location.hash === '#focus-qa') {
        const tabBtns = document.querySelectorAll("#product-details-tab .pd-tab-btn");
        tabBtns.forEach((btn, index) => {
            if (btn.innerText.includes("Soru & Cevap")) {
                if(typeof window.openTabByIndex === 'function') window.openTabByIndex(index);
            }
        });
        
        const tabContainer = document.getElementById("product-details-tab");
        if(tabContainer) {
            setTimeout(() => {
                tabContainer.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 100);
        }
        history.replaceState(null, null, ' '); // Sekme açıldıktan sonra URL'den hash'i temizle
    }

    // ---------------------------------------------------------
    // 5. EVENT DELEGATION (AJAX İLE GELEN BUTONLARI DİNLEME)
    // ---------------------------------------------------------
    // Sayfalama ve Upvote tuşları AJAX ile sonradan geldiği için
    // onları saran ana kapsayıcıya (tabQaElement) tek bir dinleyici ekliyoruz.
    if (tabQaElement) {
        tabQaElement.addEventListener('click', async (e) => {
            
            // --- A. Sayfalama Tuşları (Pagination) ---
            const pageBtn = e.target.closest('.js-qa-page-btn');
            if (pageBtn) {
                const page = pageBtn.dataset.page;
                if (page) updateQAParams('qa_page', page);
                return;
            }

            // --- B. Faydalı Bul Tuşları (Upvote) ---
            const upvoteBtn = e.target.closest('.js-upvote-btn');
            if (upvoteBtn) {
                if (upvoteBtn.dataset.loading === 'true') return; // Aynı anda çift tıklamayı önle

                const qid = upvoteBtn.dataset.qid;
                const upvoteUrl = upvoteBtn.dataset.upvoteUrl;
                const countSpan = upvoteBtn.querySelector('.js-upvote-count');
                
                if (!qid || !upvoteUrl || !countSpan) return;

                const currentCount = Number.parseInt(countSpan.textContent, 10) || 0;

                // Optimistic UI (Arayüzde sonucu anında göster)
                upvoteBtn.dataset.loading = 'true';
                upvoteBtn.style.pointerEvents = 'none';
                upvoteBtn.style.backgroundColor = 'var(--teal-50)';
                upvoteBtn.style.borderColor = 'var(--teal-500)';
                upvoteBtn.style.color = 'var(--teal-700)';
                countSpan.textContent = currentCount + 1;

                function resetUpvoteButton() {
                    upvoteBtn.style.backgroundColor = 'transparent';
                    upvoteBtn.style.borderColor = 'var(--gray-300)';
                    upvoteBtn.style.color = 'var(--gray-600)';
                    upvoteBtn.dataset.loading = 'false';
                    upvoteBtn.style.pointerEvents = 'auto';
                    countSpan.textContent = currentCount;
                }

                try {
                    const csrfTokenInput = document.querySelector('[name=csrfmiddlewaretoken]');
                    const csrfToken = csrfTokenInput ? csrfTokenInput.value : getCookie('csrftoken');

                    const response = await fetch(upvoteUrl, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-CSRFToken': csrfToken,
                            'X-Requested-With': 'XMLHttpRequest',
                            'Accept': 'application/json',
                        },
                        body: JSON.stringify({ question_id: qid }),
                    });

                    // Giriş yapılmamışsa
                    if (response.status === 401 || response.status === 403) {
                        resetUpvoteButton();
                        window.WizardUI?.showToast?.('error', 'Oy vermek için giriş yapmalısınız.');
                        return;
                    }

                    if (!response.ok) throw new Error("HTTP Hatası");

                    const data = await response.json();

                    if (!data.success) {
                        resetUpvoteButton();
                        window.WizardUI?.showToast?.('error', data.error || 'Bir sorun oluştu.');
                        return;
                    }

                    // İşlem başarılı
                    if (typeof data.count !== 'undefined') countSpan.textContent = data.count;
                    window.WizardUI?.showToast?.('success', data.message || 'Oyunuz kaydedildi.');

                } catch (error) {
                    resetUpvoteButton();
                    window.WizardUI?.showToast?.('error', 'Bağlantı hatası.');
                }
            }
        });
    }

    // ---------------------------------------------------------
    // 6. SORU SOR MODALI İŞLEMLERİ
    // ---------------------------------------------------------
    const btnOpenModal = document.getElementById('btn-open-ask-modal');
    const askModal = document.getElementById('ask-question-modal');
    const askForm = document.getElementById('ask-question-form');
    const btnSubmit = document.getElementById('btn-submit-question');
    const textEl = document.getElementById('new-q-text');
    const charCountEl = document.getElementById('qa-char-count');

    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    if (btnOpenModal && askModal && askForm) {
        const askUrl = btnOpenModal.dataset.askUrl;

        // Metin kutusu karakter sayacı ve hata temizleme
        if(textEl && charCountEl) {
            textEl.addEventListener('input', function() {
                charCountEl.textContent = this.value.length;
                this.style.borderColor = "var(--gray-300)";
                this.style.boxShadow = "none";
            });
        }

        // Varyant Kartları (Radio Buton) Seçimi
        const variantCards = document.querySelectorAll('.js-variant-card');
        variantCards.forEach(card => {
            card.addEventListener('click', function() {
                variantCards.forEach(c => c.classList.remove('active'));
                this.classList.add('active');
                const radio = this.querySelector('input[type="radio"]');
                if(radio) radio.checked = true;
            });
        });

        // Modalı Aç ve İçini Sıfırla
        btnOpenModal.addEventListener('click', (e) => {
            e.preventDefault();
            askForm.reset(); 
            
            document.getElementById('new-q-topic').value = ''; 
            const textSpan = document.querySelector('.qa-select-text');
            if (textSpan) {
                textSpan.textContent = 'Lütfen bir konu seçin'; 
                textSpan.style.color = 'var(--gray-600)';
            }
            
            if(variantCards.length > 0) {
                variantCards.forEach(c => c.classList.remove('active'));
                variantCards[0].classList.add('active');
                const firstRadio = variantCards[0].querySelector('input[type="radio"]');
                if(firstRadio) firstRadio.checked = true;
            }

            if(charCountEl) charCountEl.textContent = "0";
            const anonHelper = document.getElementById('anon-helper-text');
            if (anonHelper) anonHelper.style.display = 'none'; 
            
            const selectTrigger = document.querySelector('.qa-select-trigger');
            if (selectTrigger) {
                selectTrigger.style.borderColor = "var(--gray-300)";
                selectTrigger.style.boxShadow = "none";
            }
            if (textEl) {
                textEl.style.borderColor = "var(--gray-300)";
                textEl.style.boxShadow = "none";
            }
            
            askModal.classList.add('active');
        });

        // Modalı Kapatma Eventleri
        document.querySelectorAll('.js-modal-close').forEach(btn => {
            btn.addEventListener('click', () => askModal.classList.remove('active'));
        });

        askModal.addEventListener('mousedown', (e) => {
            if(e.target === askModal) askModal.classList.remove('active');
        });

        // Form Submit
        askForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            const topicVal = document.getElementById('new-q-topic').value;
            const selectTrigger = document.querySelector('.qa-select-trigger');
            const textVal = textEl.value.trim();

            // Validasyon: Konu
            if (!topicVal) {
                selectTrigger.style.borderColor = "var(--red-600)";
                selectTrigger.style.boxShadow = "0 0 0 3px rgba(220, 38, 38, 0.1)";
                showCustomToast("Lütfen sorunuz için bir konu seçin.", "warning");
                return;
            } else {
                selectTrigger.style.borderColor = "var(--gray-300)";
                selectTrigger.style.boxShadow = "none";
            }

            // Validasyon: Metin
            if (!textVal) {
                textEl.style.borderColor = "var(--red-600)";
                textEl.style.boxShadow = "0 0 0 3px rgba(220, 38, 38, 0.1)";
                showCustomToast("Lütfen sormak istediğiniz soruyu yazın.", "warning");
                return;
            } else {
                textEl.style.borderColor = "var(--gray-300)";
                textEl.style.boxShadow = "none";
            }
            
            const isAnon = document.getElementById('new-q-anon').checked;
            
            const checkedVariant = document.querySelector('input[name="variant_scope"]:checked');
            let variantIdToSend = null;
            if (checkedVariant && checkedVariant.value !== 'general') {
                variantIdToSend = checkedVariant.value;
            }

            const offerSelect = document.getElementById('new-q-offer-id');
            let offerIdToSend = null;
            if (offerSelect && offerSelect.value !== 'general') {
                offerIdToSend = offerSelect.value;
            }

            const originalHtml = btnSubmit.innerHTML;
            btnSubmit.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Gönderiliyor...';
            btnSubmit.disabled = true;

            try {
                const csrfTokenInput = document.querySelector('[name=csrfmiddlewaretoken]');
                const csrfToken = csrfTokenInput ? csrfTokenInput.value : getCookie('csrftoken');

                const res = await fetch(askUrl, {
                    method: 'POST',
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken
                    },
                    body: JSON.stringify({
                        product_id: productId,
                        variant_id: variantIdToSend,
                        offer_id: offerIdToSend,
                        topic: topicVal,
                        text: textVal,
                        is_anonymous: isAnon
                    })
                });

                if (res.status === 401 || res.status === 403) {
                    askModal.classList.remove('active');
                    btnSubmit.innerHTML = originalHtml;
                    btnSubmit.disabled = false;
                    
                    let isConfirmed = false;
                    if (window.WizardUI && window.WizardUI.showConfirm) {
                        isConfirmed = await window.WizardUI.showConfirm({
                            title: "Giriş Yapın",
                            message: "Soru sorabilmek için giriş yapmalısınız.",
                            confirmText: "Giriş Yap",
                            cancelText: "Kapat"
                        });
                    } else {
                        isConfirmed = confirm("Soru sorabilmek için giriş yapmalısınız. Giriş sayfasına gitmek ister misiniz?");
                    }
                    if (isConfirmed) window.location.href = `/accounts/login/?next=${window.location.pathname}#tab-qa`;
                    return;
                }

                const data = await res.json();
                
                if (data.success) {
                    askModal.classList.remove('active');
                    showCustomToast(data.message || "Sorunuz başarıyla iletildi.", "success");
                    
                    setTimeout(() => {
                        window.location.hash = 'focus-qa'; 
                        window.location.reload(); 
                    }, 1000);
                    
                } else {
                    showCustomToast(data.error || "Soru gönderilemedi.", "error");
                    btnSubmit.innerHTML = originalHtml;
                    btnSubmit.disabled = false;
                }
            } catch (err) {
                showCustomToast("Sunucu ile bağlantı kurulamadı.", "error");
                btnSubmit.innerHTML = originalHtml;
                btnSubmit.disabled = false;
            }
        });
    }

    // ---------------------------------------------------------
    // 7. ARAMALI ÖZEL KONU SEÇİCİ (CUSTOM SELECT)
    // ---------------------------------------------------------
    // Gizli isim helper yazısı
    const anonCheckbox = document.getElementById('new-q-anon');
    const anonHelper = document.getElementById('anon-helper-text');
    if (anonCheckbox && anonHelper) {
        anonCheckbox.addEventListener('change', (e) => {
            anonHelper.style.display = e.target.checked ? 'block' : 'none';
        });
    }

    const customSelect = document.querySelector('.js-custom-select');
    if (customSelect) {
        const trigger = customSelect.querySelector('.qa-select-trigger');
        const dropdown = customSelect.querySelector('.qa-select-dropdown');
        const searchInput = customSelect.querySelector('input');
        const optionsList = customSelect.querySelectorAll('.qa-select-options li');
        const hiddenInput = document.getElementById('new-q-topic');
        const textSpan = customSelect.querySelector('.qa-select-text');

        trigger.addEventListener('click', () => {
            dropdown.classList.toggle('active');
            trigger.classList.toggle('open');
            if (dropdown.classList.contains('active')) searchInput.focus();
        });

        document.addEventListener('click', (e) => {
            if (!customSelect.contains(e.target)) {
                dropdown.classList.remove('active');
                trigger.classList.remove('open');
            }
        });

        optionsList.forEach(option => {
            option.addEventListener('click', () => {
                hiddenInput.value = option.dataset.value; 
                textSpan.textContent = option.textContent;
                textSpan.style.color = "var(--gray-900)"; 
                
                trigger.style.borderColor = "var(--gray-300)";
                trigger.style.boxShadow = "none";
                
                dropdown.classList.remove('active');
                trigger.classList.remove('open');
            });
        });

        searchInput.addEventListener('input', (e) => {
            const filter = e.target.value.toLocaleLowerCase('tr-TR');
            let hasVisible = false;
            
            optionsList.forEach(option => {
                if(option.classList.contains('no-result')) return;
                
                const text = option.textContent.toLocaleLowerCase('tr-TR');
                if (text.includes(filter)) {
                    option.style.display = 'block';
                    hasVisible = true;
                } else {
                    option.style.display = 'none';
                }
            });
            
            let noResultLi = customSelect.querySelector('.no-result');
            if (!noResultLi) {
                noResultLi = document.createElement('li');
                noResultLi.className = 'no-result';
                noResultLi.textContent = 'Sonuç bulunamadı';
                customSelect.querySelector('.qa-select-options').appendChild(noResultLi);
            }
            noResultLi.style.display = hasVisible ? 'none' : 'block';
        });
    }
}

/* ----------------------------------------------------------
 * MODERN BİLDİRİM (TOAST) SİSTEMİ
 * ---------------------------------------------------------- */
function showCustomToast(message, type = "warning") {
    let container = document.querySelector('.qa-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'qa-toast-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = 'qa-toast';
    
    let iconClass = "fa-circle-exclamation";
    let colorVar = "var(--orange-500)";
    
    if (type === "error") {
        iconClass = "fa-circle-xmark";
        colorVar = "var(--red-600)";
        toast.style.borderLeftColor = colorVar;
    } else if (type === "success") {
        iconClass = "fa-circle-check";
        colorVar = "var(--green-600)";
        toast.style.borderLeftColor = colorVar;
    }

    toast.innerHTML = `<i class="fa-solid ${iconClass}" style="color: ${colorVar};"></i> <span>${message}</span>`;
    
    container.appendChild(toast);

    requestAnimationFrame(() => {
        toast.classList.add('show');
    });

    setTimeout(() => {
        toast.classList.remove('show');
        toast.addEventListener('transitionend', () => {
            if(toast.parentElement) toast.remove();
        });
    }, 3500);
}

