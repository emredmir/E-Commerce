# Sepet ER Diyagramı

Bu diyagram, mevcut Django model tanımlarındaki alanları ve ilişkileri gösterir. Model ve alan isimleri kaynak kod ile aynıdır.

```mermaid
erDiagram
    Cart {
        bigint id PK
        bigint user FK
        string session_key "Indexed"
        datetime created_at
        datetime updated_at
    }
    CartItem {
        bigint id PK
        bigint cart FK
        bigint store_product FK
        integer quantity
        boolean is_selected
        decimal last_seen_price
        datetime added_at
        datetime updated_at
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
    ProductVariant {
        bigint id PK
        bigint product FK
        string barcode UK
        bigint attribute_values
        boolean is_active
    }
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
    CustomUser {
        bigint id PK
        string password
        datetime last_login
        boolean is_superuser
        string first_name
        string last_name
        boolean is_staff
        boolean is_active
        datetime date_joined
        string email UK
        string phone_number UK
        boolean is_seller
    }
    CustomUser ||--|| Cart : "kullanıcının sepeti"
    Cart ||--o{ CartItem : "cart"
    StoreProduct ||--o{ CartItem : "sepete eklenir"
    Store ||--o{ StoreProduct : "mağazaya ait"
    ProductVariant ||--o{ StoreProduct : "varyanta ait"
    Product ||--o{ ProductVariant : "ürüne ait"
    ProductVariant ||--o{ Product : "varsayılan varyant"
    Store ||--o{ Product : "oluşturan mağaza"
```

> Not: `CartItem.store_product` doğrudan `StoreProduct`'a bağlanır; sepet fiyatı ayrı bir fiyat alanı olarak tutulmaz. `last_seen_price` yalnızca kullanıcının gördüğü son fiyatı izlemek için kullanılan snapshot alanıdır.
