#include "recon/file_parser.hpp"
#include <gtest/gtest.h>
#include <fstream>

TEST(FileParserTest, ParseAmountMinor) {
    EXPECT_EQ(recon::parse_amount_minor("0000000010000"), 10000);
    EXPECT_EQ(recon::parse_amount_minor("0000000005000"), 5000);
    EXPECT_EQ(recon::parse_amount_minor("12345"), 12345);
    EXPECT_EQ(recon::parse_amount_minor("  10000  "), 10000);
    EXPECT_EQ(recon::parse_amount_minor("-0000000010000"), -10000);
    EXPECT_EQ(recon::parse_amount_minor(""), 0);
}

TEST(FileParserTest, ParseLineValid) {
    recon::FixedWidthLayout layout;
    // Layout: ref [0,20) | amount [20,33) | currency [33,36) | status [36,43)
    std::string line = "ORDER_123456789012340000000010000INRSUCCESS";

    auto record = recon::parse_line(line, layout, recon::Source::BANK_FILE);
    ASSERT_TRUE(record.has_value());
    EXPECT_EQ(record->external_reference, "ORDER_12345678901234");
    EXPECT_EQ(record->amount_minor, 10000);
    EXPECT_EQ(record->currency, "INR");
    EXPECT_EQ(record->status, "SUCCESS");
}

TEST(FileParserTest, ParseLineInvalidReference) {
    recon::FixedWidthLayout layout;
    std::string line = "                    0000000010000INRSUCCESS";

    auto record = recon::parse_line(line, layout, recon::Source::BANK_FILE);
    EXPECT_FALSE(record.has_value());
}

TEST(FileParserTest, ParseLineInvalidAmount) {
    recon::FixedWidthLayout layout;
    std::string line = "ORDER_12345678901234000000000000INRSUCCESS";

    auto record = recon::parse_line(line, layout, recon::Source::BANK_FILE);
    EXPECT_FALSE(record.has_value());
}

TEST(FileParserTest, ParseFile) {
    std::string test_file = "/tmp/test_settlement.txt";
    {
        std::ofstream f(test_file);
        f << "ORDER_001           0000000010000INRSUCCESS\n";
        f << "ORDER_002           0000000020000INRSUCCESS\n";
        f << "ORDER_003           0000000030000INRFAILED \n";
        f << "\n";
    }

    recon::FixedWidthLayout layout;
    auto records = recon::parse_file(test_file, layout, recon::Source::BANK_FILE);

    EXPECT_EQ(records.size(), 3);
    EXPECT_EQ(records[0].external_reference, "ORDER_001");
    EXPECT_EQ(records[0].amount_minor, 10000);
    EXPECT_EQ(records[1].external_reference, "ORDER_002");
    EXPECT_EQ(records[2].external_reference, "ORDER_003");
    EXPECT_EQ(records[2].status, "FAILED");

    std::remove(test_file.c_str());
}

TEST(FileParserTest, RejectsMalformedAndOverflowAmounts) {
    EXPECT_THROW(recon::parse_amount_minor("12oops34"), std::invalid_argument);
    EXPECT_THROW(recon::parse_amount_minor("999999999999999999999"), std::invalid_argument);
    recon::FixedWidthLayout layout;
    EXPECT_FALSE(recon::parse_line("ORDER_001           00000000x1000INRPOSTED ", layout, recon::Source::BANK_FILE));
}

TEST(FileParserTest, DoesNotSilentlyDropMalformedRows) {
    const std::string path = "/tmp/recon-malformed.txt";
    { std::ofstream file(path); file << "truncated\n"; }
    recon::FixedWidthLayout layout;
    EXPECT_THROW(recon::parse_file(path, layout, recon::Source::BANK_FILE), std::runtime_error);
    std::remove(path.c_str());
}

TEST(FileParserTest, RejectsEmptyBankButAllowsEmptyLedger) {
    const std::string path = "/tmp/recon-empty.txt";
    { std::ofstream file(path); file << "\n"; }
    recon::FixedWidthLayout layout;
    EXPECT_THROW(recon::parse_file(path, layout, recon::Source::BANK_FILE), std::runtime_error);
    EXPECT_TRUE(recon::parse_file(path, layout, recon::Source::LEDGER).empty());
    std::remove(path.c_str());
}
