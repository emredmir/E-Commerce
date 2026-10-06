# Request Lifecycle Diyagramı

```mermaid
sequenceDiagram
    autonumber
    participant B as Browser
    participant U as URL Router
    participant V as View / APIView
    participant F as Form / Validation
    participant S as Domain Service
    participant ORM as Django ORM
    participant DB as MSSQL
    participant X as External Service

    B->>U: HTTP Request
    U->>V: Uygun View'e yönlendir
    V->>F: Input doğrulama (gerekiyorsa)
    F-->>V: Validated data
    V->>S: Business operation
    S->>ORM: Query / create / update
    ORM->>DB: SQL işlemi
    DB-->>ORM: Sonuç
    ORM-->>S: Model / QuerySet / result

    opt Dış servis gerekiyorsa
        S->>X: API çağrısı
        X-->>S: API sonucu
    end

    S-->>V: Domain result / exception
    V-->>B: HTML / JSON / Redirect / Error response
```

## Temel prensip

View; HTTP'nin dilini, service ise domain'in dilini konuşur. Böylece business logic'in doğrudan template veya HTTP response üretmesine bağımlı olması azaltılır.
