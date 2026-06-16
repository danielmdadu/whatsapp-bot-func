"""
SQL Server Connection Test Script

This script tests connectivity to a SQL Server database and displays
all rows and columns from a specified table.

Usage:
    python test_sql_connection.py

Before running, ensure you have:
1. Installed pyodbc: pip install pyodbc
2. Installed ODBC Driver 18 for SQL Server (on macOS: brew install microsoft/mssql-release/msodbcsql18)
3. Set the required environment variables in your .env file:
   - PRICES_SQL_SERVER: Your SQL Server hostname (e.g., myserver.database.windows.net)
   - PRICES_SQL_DATABASE: Database name
   - PRICES_SQL_USERNAME: Username for authentication
   - PRICES_SQL_PASSWORD: Password for authentication
"""

import os
import sys

# Add parent directory to path to access shared modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

try:
    import pyodbc
except ImportError:
    print("ERROR: pyodbc is not installed.")
    print("Please install it with: pip install pyodbc")
    print("\nOn macOS, you also need the ODBC driver:")
    print("  brew install microsoft/mssql-release/msodbcsql18")
    sys.exit(1)


def get_connection_string():
    """Build the SQL Server connection string from environment variables."""
    server = os.environ.get('PRICES_SQL_SERVER')
    database = os.environ.get('PRICES_SQL_DATABASE')
    username = os.environ.get('PRICES_SQL_USERNAME')
    password = os.environ.get('PRICES_SQL_PASSWORD')
    
    # Validate required environment variables
    missing = []
    if not server:
        missing.append('PRICES_SQL_SERVER')
    if not database:
        missing.append('PRICES_SQL_DATABASE')
    if not username:
        missing.append('PRICES_SQL_USERNAME')
    if not password:
        missing.append('PRICES_SQL_PASSWORD')
    
    if missing:
        print(f"ERROR: Missing required environment variables: {', '.join(missing)}")
        print("\nPlease add them to your .env file:")
        for var in missing:
            print(f"  {var}=your_value_here")
        sys.exit(1)
    
    connection_string = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server={server};"
        f"Database={database};"
        f"Uid={username};"
        f"Pwd={password};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=yes;"
    )
    
    return connection_string


def test_connection():
    """Test the database connection."""
    print("=" * 60)
    print("SQL Server Connection Test")
    print("=" * 60)
    
    connection_string = get_connection_string()
    
    print(f"\nConnecting to: {os.environ.get('PRICES_SQL_SERVER')}")
    print(f"Database: {os.environ.get('PRICES_SQL_DATABASE')}")
    print(f"User: {os.environ.get('PRICES_SQL_USERNAME')}")
    print("-" * 60)
    
    try:
        conn = pyodbc.connect(connection_string, timeout=30)
        print("✅ Connection successful!\n")
        return conn
    except pyodbc.Error as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)


def list_tables(conn):
    """List all available tables in the database."""
    cursor = conn.cursor()
    
    print("Available tables:")
    print("-" * 40)
    
    tables = []
    for row in cursor.tables(tableType='TABLE'):
        table_name = row.table_name
        schema = row.table_schem
        tables.append((schema, table_name))
        print(f"  {schema}.{table_name}")
    
    print()
    return tables


def display_table_data(conn, table_name, schema='dbo', max_rows=50):
    """
    Display all rows and columns from a specified table.
    
    Args:
        conn: Database connection
        table_name: Name of the table to query
        schema: Schema name (default: 'dbo')
        max_rows: Maximum number of rows to display (default: 50)
    """
    cursor = conn.cursor()
    
    full_table_name = f"[{schema}].[{table_name}]"
    
    print(f"\nQuerying table: {full_table_name}")
    print("=" * 60)
    
    try:
        # Get column information
        cursor.execute(f"SELECT TOP {max_rows} * FROM {full_table_name}")
        
        # Get column names
        columns = [column[0] for column in cursor.description]
        print(f"\nColumns ({len(columns)}):")
        for i, col in enumerate(columns, 1):
            print(f"  {i}. {col}")
        
        print(f"\n{'=' * 60}")
        print(f"Data (showing up to {max_rows} rows):")
        print("-" * 60)
        
        rows = cursor.fetchall()
        
        if not rows:
            print("  (No data found)")
        else:
            for i, row in enumerate(rows, 1):
                print(f"\n--- Row {i} ---")
                for col, value in zip(columns, row):
                    print(f"  {col}: {value}")
        
        print(f"\n{'=' * 60}")
        print(f"Total rows displayed: {len(rows)}")
        
    except pyodbc.Error as e:
        print(f"❌ Query failed: {e}")


def main():
    """Main function to run the SQL Server connection test."""
    # Target table configuration
    TARGET_SCHEMA = 'dbo'
    TARGET_TABLE = 'inventario_odoo_chatbot'
    
    # Test connection
    conn = test_connection()
    
    # Display data from the target table
    display_table_data(conn, TARGET_TABLE, TARGET_SCHEMA)
    
    # Close connection
    conn.close()
    print("\nConnection closed.")


if __name__ == "__main__":
    main()
