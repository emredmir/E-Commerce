# Ürün Taslağı, Koleksiyon ve Soru-Cevap ER Diyagramı

Bu diyagram, mevcut Django model tanımlarındaki alanları ve ilişkileri gösterir. Model ve alan isimleri kaynak kod ile aynıdır.

```mermaid
erDiagram
    Product {
        bigint id PK
        string name
        string slug UK
        text description
        bigint category FK
        bigint brand FK
        datetime created_at
        string status "Indexed"
        bigint default_variant FK
        bigint created_by_store FK
        string normalized_name "Indexed"
        string normalized_key "Indexed"
        json tokens
    }
    AttributeValue {
        bigint id PK
        bigint attribute FK
        string value
        boolean is_active
    }
    Store {
        bigint id PK
        bigint seller FK
        string store_name
        string slug UK
        image logo
        image banner
        string contact_email
        string contact_phone
        string address
        string status
        boolean is_active
        datetime created_at
        datetime updated_at
        datetime approved_at
    }
    ProductDraft {
        bigint id PK
        bigint seller FK
        bigint store FK
        string name
        bigint category FK
        bigint brand FK
        text description
        string normalized_name "Indexed"
        string normalized_key "Indexed"
        json tokens
        bigint matched_product FK
        string match_status "Indexed"
        bigint published_product FK
        smallint last_completed_step
        smallint current_step
        string status "Indexed"
        datetime completed_at
        datetime created_at
        datetime updated_at
    }
    ProductDraftVariant {
        bigint id PK
        bigint draft FK
        string sku "Indexed"
        string barcode "Indexed"
        decimal price
        integer stock
        boolean is_default
        bigint attribute_values
        integer sort_order
        boolean is_active
        datetime created_at
        datetime updated_at
    }
    ProductDraftImageGroup {
        bigint id PK
        bigint draft FK
        bigint visual_attribute_values
        integer sort_order
        datetime created_at
        boolean is_active
        datetime updated_at
    }
    ProductDraftImage {
        bigint id PK
        bigint group FK
        image image
        string alt_text
        integer sort_order
        boolean is_active
        boolean is_main
        string file_hash "Indexed"
        datetime created_at
    }
    ProductCollection {
        bigint id PK
        bigint user FK
        string name
        boolean is_default
        datetime created_at
    }
    ProductCollectionItem {
        bigint id PK
        bigint collection FK
        bigint variant FK
        bigint offer FK
        datetime added_at
    }
    ProductVariant {
        bigint id PK
        bigint product FK
        string barcode UK
        bigint attribute_values
        boolean is_active
    }
    StoreProduct {
        bigint id PK
        bigint store FK
        bigint variant FK
        string sku "Indexed"
        decimal price
        integer stock
        integer sold_count
        text seller_notes
        string status
        datetime created_at
        datetime updated_at
    }
    ProductQuestion {
        bigint id PK
        bigint product FK
        bigint user FK
        bigint target_store FK
        bigint variant_context FK
        string topic
        text text
        boolean is_visible
        boolean is_anonymous
        integer upvotes
        datetime created_at
    }
    ProductQuestionUpvote {
        bigint id PK
        bigint question FK
        bigint user FK
        datetime created_at
    }
    ProductAnswer {
        bigint id PK
        bigint question FK
        bigint store FK
        bigint user FK
        text text
        boolean is_visible
        datetime created_at
        datetime updated_at
        boolean is_read_by_user
    }
    ProductVariant ||--o{ Product : "varsayılan varyant"
    Store ||--o{ Product : "oluşturan mağaza"
    Store ||--o{ ProductDraft : "taslakları"
    Product ||--o{ ProductDraft : "eşleşen ürün"
    Product ||--o{ ProductDraft : "yayınlanan eşleşmeler"
    ProductDraft ||--o{ ProductDraftVariant : "varyantları"
    ProductDraftVariant }o--o{ AttributeValue : "özellik değerleri"
    ProductDraft ||--o{ ProductDraftImageGroup : "görsel grupları"
    ProductDraftImageGroup }o--o{ AttributeValue : "görsel özellik değerleri"
    ProductDraftImageGroup ||--o{ ProductDraftImage : "görselleri"
    ProductCollection ||--o{ ProductCollectionItem : "öğeleri"
    ProductVariant ||--o{ ProductCollectionItem : "koleksiyon öğeleri"
    StoreProduct ||--o{ ProductCollectionItem : "eklenen teklifler"
    Product ||--o{ ProductVariant : "ürüne ait"
    ProductVariant }o--o{ AttributeValue : "özellik değerleri"
    Store ||--o{ StoreProduct : "mağazaya ait"
    ProductVariant ||--o{ StoreProduct : "varyanta ait"
    Product ||--o{ ProductQuestion : "soruları"
    Store ||--o{ ProductQuestion : "yöneltilen sorular"
    ProductVariant ||--o{ ProductQuestion : "bağlam varyantı"
    ProductQuestion ||--o{ ProductQuestionUpvote : "faydalı oyları"
    ProductQuestion ||--o{ ProductAnswer : "cevapları"
    Store ||--o{ ProductAnswer : "verdiği cevaplar"
```
