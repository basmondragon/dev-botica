# Botica

One system for running a chain of Colombian pharmacies: what is in stock, what sells, what to buy, what to charge, and how the network is doing.

Today those pharmacies run on desktop software written fifteen years ago, spreadsheets, and the owner's memory. Every branch is an island — finding out whether another one has a box in stock means calling it.

## Who uses it

- **The cashier**, at the counter, serving someone who is waiting.
- **The administrator**, running the network day to day.
- **The owner**, who wants to know how the business is doing without asking anyone for a report.

## What it does

- Live inventory across every branch, by lot and expiry date
- A counter that sells fast, on a keyboard and a barcode scanner
- Purchasing that says what to buy, how much, and why
- Pricing that shows what a product's price is actually worth changing
- An assistant that suggests what to offer, from what that branch has on the shelf right now
- One view of the whole network, per branch and together

## Rules it must not break

1. **The counter sells when the internet is down.** It reconciles by itself when the connection returns. Nobody is asked what to do about it.
2. **Stock is never a number someone typed over.** Every unit traces to the movement that caused it, the person who did it and the moment it happened.
3. **The counter is fast.** Nothing between scanning a barcode and the line appearing on the ticket touches the network.
4. **Botica does not invoice for anyone.** It hands a complete, correct sale to whatever system the pharmacy already invoices with, exactly once.
5. **Models suggest, people decide.** No model changes a price, and the difference between what was suggested and what was chosen is recorded.
6. **The assistant does not diagnose.** It never suggests a product the safety rules exclude, and it says so when a customer should see a doctor.

## What it is not

Not accounting, not a CRM, not a patient record, not a delivery app, and not a program anyone has to install. It runs in a browser.

## Where things are

|                         |                                            |
| ----------------------- | ------------------------------------------ |
| `.docs/architecture.md` | the authority — what the system is and why |
| `.docs/stages/`         | how it gets built, in order                |
| `.docs/handoff/`        | the designed screens                       |
| `.docs/brief.pdf`       | the client deck                            |
