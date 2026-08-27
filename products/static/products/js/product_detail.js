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