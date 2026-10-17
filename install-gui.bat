@echo off
setlocal DisableDelayedExpansion
rem Keep the GUI entry explicit while preserving source paths containing percent signs.
"%~dp0dependency_manager.cmd" -Gui %*
