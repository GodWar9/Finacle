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
    std::string line = "ORDER_12345678901234  0000000010000INR SUCCESS  ";
    
    auto record = recon::parse_line(line, layout, recon::Source::BANK_FILE);
    ASSERT_TRUE(record.has_value());
    EXPECT_EQ(record->external_reference, "ORDER_12345678901234");
    EXPECT_EQ(record->amount_minor, 10000);
    EXPECT_EQ(record->currency, "INR");
    EXPECT_EQ(record->status, "SUCCESS");
}

TEST(FileParserTest, ParseLineInvalidReference) {
    recon::FixedWidthLayout layout;
    std::string line = "                    0000000010000INR SUCCESS  ";
    
    auto record = recon::parse_line(line, layout, recon::Source::BANK_FILE);
    EXPECT_FALSE(record.has_value());
}

TEST(FileParserTest, ParseLineInvalidAmount) {
    recon::FixedWidthLayout layout;
    std::string line = "ORDER_12345678901234  0000000000000INR SUCCESS  ";
    
    auto record = recon::parse_line(line, layout, recon::Source::BANK_FILE);
    EXPECT_FALSE(record.has_value());
}

TEST(FileParserTest, ParseFile) {
    std::string test_file = "/tmp/test_settlement.txt";
    {
        std::ofstream f(test_file);
        f << "ORDER_001              0000000010000INR SUCCESS  \n";
        f << "ORDER_002              0000000020000INR SUCCESS  \n";
        f << "ORDER_003              0000000030000INR FAILED   \n";
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