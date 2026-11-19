#include <cstdlib>
#include <exception>
#include <iostream>
#include <string_view>

#include <lsp/io/standard_io.h>
#include <lsp/server_endpoint.h>
#include <plangd/lsp_impl/server.hpp>

#include "version.hpp"

/// Starts the stdio server or prints its version when requested.
int main(int argc, char** argv) {
  if (argc == 2 && std::string_view(argv[1]) == "--version") {
    std::cout << "plangd " << plangd::version << '\n';
    return EXIT_SUCCESS;
  }
  if (argc != 1) {
    std::cerr << "Usage: plangd [--version]\n";
    return EXIT_FAILURE;
  }

  try {
    bool shutdown_requested = false;
    lsp::ServerEndpoint endpoint(lsp::io::standardIO());
    plangd::registerLifecycleHandlers(endpoint, plangd::version,
                                      shutdown_requested);
    endpoint.runMessageLoop();
    return shutdown_requested ? EXIT_SUCCESS : EXIT_FAILURE;
  } catch (const std::exception& error) {
    std::cerr << "plangd: " << error.what() << '\n';
    return EXIT_FAILURE;
  }
}
