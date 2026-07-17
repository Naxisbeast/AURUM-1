"""AURUM-1 entry point.

The D4 paper trader is the primary trading system.
Run directly: python -m scripts.paper_trading.d4_paper_trader
"""

if __name__ == "__main__":
    from scripts.paper_trading.d4_paper_trader import main
    main()
