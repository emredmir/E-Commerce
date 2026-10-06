# Kimlik ve Mağaza ER Diyagramı

Bu diyagram, mevcut Django model tanımlarındaki alanları ve ilişkileri gösterir. Model ve alan isimleri kaynak kod ile aynıdır.

```mermaid
erDiagram
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
    Address {
        bigint id PK
        bigint user FK
        string title
        string full_name
        string phone_number
        string address_line1
        string address_line2
        string city
        string state
        string postal_code
        boolean is_default
        datetime created_at
    }
    SellerProfile {
        bigint id PK
        bigint user FK
        string seller_type
        string company_name
        string legal_company_title
        text company_address
        string company_phone
        string tax_office
        string tax_number
        string identity_number
        string iban
        boolean is_approved
        string iyzico_submerchant_external_id UK
        string iyzico_submerchant_key
        datetime iyzico_onboarding_started_at
        string iyzico_onboarding_status
        datetime iyzico_onboarded_at
        datetime iyzico_last_sync_at
        string iyzico_last_error_code
        text iyzico_last_error_message
        datetime created_at
    }
    SellerProfileUpdateRequest {
        bigint id PK
        bigint seller_profile FK
        string new_company_name
        text new_company_address
        string new_company_phone
        string new_iban
        string status
        datetime created_at
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
    StoreUpdateRequest {
        bigint id PK
        bigint store FK
        string new_store_name
        image new_logo
        image new_banner
        string new_contact_email
        string new_contact_phone
        string new_address
        string status
        datetime created_at
    }
    BrandRequest {
        bigint id PK
        bigint seller FK
        bigint category FK
        string brand_name
        text note
        string status "Indexed"
        bigint reviewed_by FK
        datetime reviewed_at
        datetime created_at
        datetime last_activity_at
    }
    CustomUser ||--o{ Address : "adresleri"
    CustomUser ||--|| SellerProfile : "satıcı profili"
    SellerProfile ||--o{ SellerProfileUpdateRequest : "değişiklik talepleri"
    SellerProfile ||--o{ Store : "mağazaları"
    Store ||--o{ StoreUpdateRequest : "değişiklik talepleri"
    CustomUser ||--o{ BrandRequest : "talep eder"
    CustomUser ||--o{ BrandRequest : "inceler"
```
