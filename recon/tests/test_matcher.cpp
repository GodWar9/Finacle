#include "recon/matcher.hpp"
#include "recon/types.hpp"
#include <gtest/gtest.h>
#include <vector>

static recon::SettlementRecord make_record(const std::string& ref, int64_t amount, const std::string& status, recon::Source source) {
    recon::SettlementRecord r;
    r.external_reference = ref;
    r.amount_minor = amount;
    r.currency = "INR";
    r.status = status;
    r.source = source;
    return r;
}

TEST(MatcherTest, PerfectMatch) {
    recon::MatchConfig config;
    config.amount_tolerance_minor = 0;
    
    std::vector<recon::SettlementRecord> ledger = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::LEDGER),
        make_record("ORDER_002", 20000, "SUCCESS", recon::Source::LEDGER),
    };
    
    std::vector<recon::SettlementRecord> bank = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::BANK_FILE),
        make_record("ORDER_002", 20000, "SUCCESS", recon::Source::BANK_FILE),
    };
    
    recon::Matcher matcher(config);
    auto exceptions = matcher.reconcile(ledger, bank, "batch_001");
    
    EXPECT_EQ(exceptions.size(), 0);
}

TEST(MatcherTest, MissingInLedger) {
    recon::MatchConfig config;
    config.amount_tolerance_minor = 0;
    
    std::vector<recon::SettlementRecord> ledger = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::LEDGER),
    };
    
    std::vector<recon::SettlementRecord> bank = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::BANK_FILE),
        make_record("ORDER_002", 20000, "SUCCESS", recon::Source::BANK_FILE),
    };
    
    recon::Matcher matcher(config);
    auto exceptions = matcher.reconcile(ledger, bank, "batch_001");
    
    EXPECT_EQ(exceptions.size(), 1);
    EXPECT_EQ(exceptions[0].type, recon::ExceptionType::MISSING_IN_LEDGER);
    EXPECT_EQ(exceptions[0].bank_reference, "ORDER_002");
}

TEST(MatcherTest, MissingInBankFile) {
    recon::MatchConfig config;
    config.amount_tolerance_minor = 0;
    
    std::vector<recon::SettlementRecord> ledger = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::LEDGER),
        make_record("ORDER_002", 20000, "SUCCESS", recon::Source::LEDGER),
    };
    
    std::vector<recon::SettlementRecord> bank = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::BANK_FILE),
    };
    
    recon::Matcher matcher(config);
    auto exceptions = matcher.reconcile(ledger, bank, "batch_001");
    
    EXPECT_EQ(exceptions.size(), 1);
    EXPECT_EQ(exceptions[0].type, recon::ExceptionType::MISSING_IN_BANK_FILE);
    EXPECT_EQ(exceptions[0].ledger_transaction_id, "ORDER_002");
}

TEST(MatcherTest, AmountMismatch) {
    recon::MatchConfig config;
    config.amount_tolerance_minor = 0;
    
    std::vector<recon::SettlementRecord> ledger = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::LEDGER),
    };
    
    std::vector<recon::SettlementRecord> bank = {
        make_record("ORDER_001", 10050, "SUCCESS", recon::Source::BANK_FILE),
    };
    
    recon::Matcher matcher(config);
    auto exceptions = matcher.reconcile(ledger, bank, "batch_001");
    
    EXPECT_EQ(exceptions.size(), 1);
    EXPECT_EQ(exceptions[0].type, recon::ExceptionType::AMOUNT_MISMATCH);
    EXPECT_EQ(exceptions[0].ledger_amount_minor, 10000);
    EXPECT_EQ(exceptions[0].bank_amount_minor, 10050);
}

TEST(MatcherTest, AmountMismatchWithinTolerance) {
    recon::MatchConfig config;
    config.amount_tolerance_minor = 100;
    
    std::vector<recon::SettlementRecord> ledger = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::LEDGER),
    };
    
    std::vector<recon::SettlementRecord> bank = {
        make_record("ORDER_001", 10050, "SUCCESS", recon::Source::BANK_FILE),
    };
    
    recon::Matcher matcher(config);
    auto exceptions = matcher.reconcile(ledger, bank, "batch_001");
    
    EXPECT_EQ(exceptions.size(), 0);
}

TEST(MatcherTest, StatusMismatch) {
    recon::MatchConfig config;
    config.amount_tolerance_minor = 0;
    
    std::vector<recon::SettlementRecord> ledger = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::LEDGER),
    };
    
    std::vector<recon::SettlementRecord> bank = {
        make_record("ORDER_001", 10000, "FAILED", recon::Source::BANK_FILE),
    };
    
    recon::Matcher matcher(config);
    auto exceptions = matcher.reconcile(ledger, bank, "batch_001");
    
    EXPECT_EQ(exceptions.size(), 1);
    EXPECT_EQ(exceptions[0].type, recon::ExceptionType::STATUS_MISMATCH);
}

TEST(MatcherTest, DuplicateInBank) {
    recon::MatchConfig config;
    config.amount_tolerance_minor = 0;
    
    std::vector<recon::SettlementRecord> ledger = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::LEDGER),
    };
    
    std::vector<recon::SettlementRecord> bank = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::BANK_FILE),
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::BANK_FILE),
    };
    
    recon::Matcher matcher(config);
    auto exceptions = matcher.reconcile(ledger, bank, "batch_001");
    
    EXPECT_GE(exceptions.size(), 1);
    bool has_duplicate = false;
    for (const auto& e : exceptions) {
        if (e.type == recon::ExceptionType::DUPLICATE) {
            has_duplicate = true;
            break;
        }
    }
    EXPECT_TRUE(has_duplicate);
}

TEST(MatcherTest, MultipleExceptions) {
    recon::MatchConfig config;
    config.amount_tolerance_minor = 0;
    
    std::vector<recon::SettlementRecord> ledger = {
        make_record("ORDER_001", 10000, "SUCCESS", recon::Source::LEDGER),
        make_record("ORDER_002", 20000, "SUCCESS", recon::Source::LEDGER),
    };
    
    std::vector<recon::SettlementRecord> bank = {
        make_record("ORDER_001", 10050, "SUCCESS", recon::Source::BANK_FILE),
        make_record("ORDER_003", 30000, "SUCCESS", recon::Source::BANK_FILE),
    };
    
    recon::Matcher matcher(config);
    auto exceptions = matcher.reconcile(ledger, bank, "batch_001");
    
    EXPECT_EQ(exceptions.size(), 3);
    
    int amount_mismatch = 0, missing_ledger = 0, missing_bank = 0;
    for (const auto& e : exceptions) {
        if (e.type == recon::ExceptionType::AMOUNT_MISMATCH) amount_mismatch++;
        if (e.type == recon::ExceptionType::MISSING_IN_LEDGER) missing_ledger++;
        if (e.type == recon::ExceptionType::MISSING_IN_BANK_FILE) missing_bank++;
    }
    
    EXPECT_EQ(amount_mismatch, 1);
    EXPECT_EQ(missing_ledger, 1);
    EXPECT_EQ(missing_bank, 1);
}