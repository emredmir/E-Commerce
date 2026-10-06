# Stock Reservation Concurrency

```mermaid
sequenceDiagram
    actor A as Request A
    actor B as Request B
    participant DB as MSSQL
    participant O as Order
    participant SP as StoreProduct
    participant SR as StockReservation

    A->>DB: BEGIN
    A->>O: SELECT ... FOR UPDATE
    A->>SP: SELECT ... FOR UPDATE
    A->>SR: SELECT ... FOR UPDATE
    A->>A: Reservation state + stock validation
    A->>SP: Stok tüket
    A->>SR: ACTIVE -> CONSUMED
    A->>DB: COMMIT

    B->>DB: BEGIN
    B->>O: SELECT ... FOR UPDATE
    Note over B,O: A commit edene kadar bekler
    B->>SP: SELECT ... FOR UPDATE
    B->>SR: SELECT ... FOR UPDATE
    B->>B: Güncel state ile tekrar doğrula

    alt Stok / reservation uygun
        B->>SP: Stok tüket
        B->>SR: ACTIVE -> CONSUMED
        B->>DB: COMMIT
    else Uygun değil
        B->>DB: ROLLBACK
    end
```

## Amaç

İki paralel request'in aynı eski stock değerini okuyup aynı ürünü satmasını engellemek.

## Lock sırası

```text
Order
 ↓
StoreProduct
 ↓
StockReservation
```

Aynı resource set'ine dokunan operasyonlarda bu sıranın korunması deadlock riskini azaltır.
