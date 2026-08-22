# CrossFX — Business and Technical Specification

*ECO5040W — Financial Software Engineering, UCT — Group 4*
*Ndumiso Zondi (ZNDNDU007) · Marco Klopper (KLPMAR012) · Muki Mdluli (MDLMUK001) · Rafaela Stevenson (STVRAF001)*

> Target length: ~10–15 pages. Fill in each section below as the design solidifies.
> Due alongside the check-in on 18 September.

## 1. Business Problem

## 2. User Journey

## 3. Functional Requirements

## 4. Fee Model

## 5. Exchange-Rate Calculation

## 6. Remittance Limits

## 7. Architecture Diagram

## 8. Cash-In Flow

## 9. RLUSD Settlement Flow

## 10. Cash-Out Flow

## 11. Database Design

## 12. API Overview

## 13. Security Design

- Password hashing (bcrypt)
- Private key encryption at rest, key stored separately from the DB
- Custodial wallet approach (per-user XRPL account vs. pooled platform wallet) — state and justify the choice here

## 14. Regulatory Considerations

- KYC / AML
- Transaction monitoring
- Customer transaction limits
- Protection of customer information (POPIA)
- Custody of crypto assets
- Stablecoin / crypto-asset regulation
- Foreign-exchange and capital-flow controls (SARB / Exchange Control Regulations)
- Consumer protection
- Safeguarding of customer funds
- Licensing considerations for a real remittance service (e.g. FSCA, NPS Act, Reserve Bank authorisation)

## 15. Assumptions and Limitations
