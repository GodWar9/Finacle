#include "recon/types.hpp"
#include "recon/file_parser.hpp"
#include "recon/matcher.hpp"
#include "recon/db_writer.hpp"
#include <iostream>
#include <string>
#include <vector>
#include <algorithm>
#include <cstdlib>
#include <chrono>
#include <random>
#include <sstream>

static std::string generate_batch_id() {
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(0, 15);
    
    std::stringstream ss;
    for (int i = 0; i < 32; ++i) {
        if (i == 8 || i == 12 || i == 16 || i == 20) ss << "-";
        ss << std::hex << dis(gen);
    }
    return ss.str();
}

static void print_usage(const char* program) {
    std::cerr << "Usage: " << program << " --ledger-export=<file> --bank-file=<file> [--batch-id=<id>] [--tolerance=<minor_units>]\n"
              << "  --ledger-export  Path to ledger export file (fixed-width)\n"
              << "  --bank-file      Path to bank settlement file (fixed-width)\n"
              << "  --batch-id       Optional batch ID (auto-generated if not provided)\n"
              << "  --tolerance      Amount tolerance in minor units (default: 0)\n";
}

int main(int argc, char* argv[]) {
    std::string ledger_file;
    std::string bank_file;
    std::string batch_id;
    int64_t tolerance = 0;
    
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg.rfind("--ledger-export=", 0) == 0) {
            ledger_file = arg.substr(16);
        } else if (arg.rfind("--bank-file=", 0) == 0) {
            bank_file = arg.substr(12);
        } else if (arg.rfind("--batch-id=", 0) == 0) {
            batch_id = arg.substr(11);
        } else if (arg.rfind("--tolerance=", 0) == 0) {
            tolerance = std::stoll(arg.substr(12));
        } else if (arg == "--help" || arg == "-h") {
            print_usage(argv[0]);
            return 0;
        }
    }
    
    if (ledger_file.empty() || bank_file.empty()) {
        print_usage(argv[0]);
        return 1;
    }
    
    if (batch_id.empty()) {
        batch_id = generate_batch_id();
    }
    
    const char* db_url = std::getenv("DATABASE_URL");
    if (!db_url) {
        std::cerr << "DATABASE_URL environment variable not set\n";
        return 1;
    }
    
    try {
        recon::FixedWidthLayout layout;
        
        std::cout << "Parsing ledger export: " << ledger_file << std::endl;
        auto ledger_records = recon::parse_file(ledger_file, layout, recon::Source::LEDGER);
        std::cout << "Parsed " << ledger_records.size() << " ledger records\n";
        
        std::cout << "Parsing bank file: " << bank_file << std::endl;
        auto bank_records = recon::parse_file(bank_file, layout, recon::Source::BANK_FILE);
        std::cout << "Parsed " << bank_records.size() << " bank records\n";
        
        recon::MatchConfig config;
        config.amount_tolerance_minor = tolerance;
        
        recon::Matcher matcher(config);
        
        auto start = std::chrono::high_resolution_clock::now();
        auto exceptions = matcher.reconcile(ledger_records, bank_records, batch_id);
        auto end = std::chrono::high_resolution_clock::now();
        
        auto duration = std::chrono::duration_cast<std::chrono::milliseconds>(end - start);
        std::cout << "Reconciliation completed in " << duration.count() << " ms\n";
        std::cout << "Exceptions found: " << exceptions.size() << "\n";
        
        size_t matched_count = bank_records.size()
                               - std::count_if(exceptions.begin(), exceptions.end(),
                                   [](const auto& e) { return e.type == recon::ExceptionType::MISSING_IN_LEDGER; });
        
        recon::DbWriter writer(db_url);
        writer.write_exceptions(exceptions);
        writer.write_batch_summary(batch_id, matched_count, exceptions.size());
        
        std::cout << "Results written to database. Batch ID: " << batch_id << "\n";
        
        for (const auto& exc : exceptions) {
            std::cout << "  [" << recon::exception_type_to_string(exc.type) << "] "
                      << "Ref: " << exc.bank_reference
                      << ", Ledger: " << exc.ledger_amount_minor
                      << ", Bank: " << exc.bank_amount_minor << "\n";
        }
        
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << "\n";
        return 1;
    }
    
    return 0;
}