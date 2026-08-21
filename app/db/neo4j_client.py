import os
from neo4j import GraphDatabase
import logging

logger = logging.getLogger(__name__)

class Neo4jClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Neo4jClient, cls).__new__(cls)
            cls._instance._init_driver()
        return cls._instance

    def _init_driver(self):
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USERNAME", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "password")
        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        
        try:
            self.driver = GraphDatabase.driver(uri, auth=(user, password))
            # verify connectivity
            self.driver.verify_connectivity()
            logger.info("Connected to Neo4j successfully.")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j: {e}")
            self.driver = None

    def close(self):
        if self.driver:
            self.driver.close()

    def execute_query(self, query: str, parameters: dict = None):
        if not self.driver:
            logger.error("No Neo4j driver available.")
            return None
            
        parameters = parameters or {}
        try:
            records, summary, keys = self.driver.execute_query(
                query,
                parameters_=parameters,
                database_=self.database
            )
            return {"data": [dict(r) for r in records]}
        except Exception as e:
            logger.error(f"Error executing Neo4j query: {e}")
            return None

neo4j_client = Neo4jClient()
