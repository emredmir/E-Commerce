# Genel Veritabanı ER Diyagramı

Bu, 44 modelin tamamını tek kutuya doldurmak yerine domainler arasındaki ana veri akışını gösteren özet diyagramdır. Ayrıntılı alanlar domain bazlı diyagramlarda bulunur.

```mermaid
flowchart TD
    U[CustomUser] --> A[Address]
    U --> SP[SellerProfile]
    SP --> S[Store]
    S --> O[StoreProduct]
    P[Product] --> V[ProductVariant]
    V --> O
    O --> C[CartItem]
    C --> CART[Cart]
    C --> OI[OrderItem]
    ORD[Order] --> SO[SubOrder]
    SO --> OI
    OI --> SR[StockReservation]
    SO --> INV[Invoice]
    INV --> II[InvoiceItem]
    ORD --> PT[PaymentTransaction]
    PT --> PTI[PaymentTransactionItem]
    PT --> PR[PaymentRefund]
    PR --> PRI[PaymentRefundItem]
    SO --> CAN[SubOrderCancellation]
    U --> PC[PaymentCustomer]
    PC --> SC[StoredCard]
    U --> SCO[StoredCardOperation]
    SC --> SCO
    P --> Q[ProductQuestion]
    Q --> QA[ProductAnswer]
```

## Ayrıntılı Diyagramlar

- [Kimlik ve Mağaza](identity-store-er.md)
- [Katalog ve Satıcı Teklifleri](catalog-core-er.md)
- [Ürün Taslağı, Koleksiyon ve Soru-Cevap](product-engagement-er.md)
- [Sepet](cart-er.md)
- [Sipariş ve Fatura](order-er.md)
- [Ödeme ve Refund](payment-er.md)
