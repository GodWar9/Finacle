#include "recon/types.hpp"
#include <gtest/gtest.h>

TEST(TypesTest, ExceptionTypeToString) {
    EXPECT_EQ(recon::exception_type_to_string(recon::ExceptionType::MISSING_IN_LEDGER), "MISSING_IN_LEDGER");
    EXPECT_EQ(recon::exception_type_to_string(recon::ExceptionType::MISSING_IN_BANK_FILE), "MISSING_IN_BANK_FILE");
    EXPECT_EQ(recon::exception_type_to_string(recon::ExceptionType::AMOUNT_MISMATCH), "AMOUNT_MISMATCH");
    EXPECT_EQ(recon::exception_type_to_string(recon::ExceptionType::DUPLICATE), "DUPLICATE");
    EXPECT_EQ(recon::exception_type_to_string(recon::ExceptionType::STATUS_MISMATCH), "STATUS_MISMATCH");
}

TEST(TypesTest, ExceptionTypeFromString) {
    EXPECT_EQ(recon::exception_type_from_string("MISSING_IN_LEDGER"), recon::ExceptionType::MISSING_IN_LEDGER);
    EXPECT_EQ(recon::exception_type_from_string("AMOUNT_MISMATCH"), recon::ExceptionType::AMOUNT_MISMATCH);
    
    EXPECT_THROW(recon::exception_type_from_string("INVALID"), std::invalid_argument);
}

TEST(TypesTest, SourceToString) {
    EXPECT_EQ(recon::source_to_string(recon::Source::LEDGER), "LEDGER");
    EXPECT_EQ(recon::source_to_string(recon::Source::PAYMENT_GATEWAY), "PAYMENT_GATEWAY");
    EXPECT_EQ(recon::source_to_string(recon::Source::BANK_FILE), "BANK_FILE");
}