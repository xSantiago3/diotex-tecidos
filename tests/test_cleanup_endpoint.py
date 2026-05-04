"""
Testes para o endpoint de limpeza de pedidos expirados
"""
import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from app.main import app
from app.config import get_settings


client = TestClient(app)
settings = get_settings()


class TestCleanupEndpointAuthentication:
    """Testes de autenticação do endpoint de cleanup"""
    
    def test_cleanup_without_token_returns_401(self):
        """Deve retornar 401 quando token não é fornecido"""
        response = client.post(
            "/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=50"
        )
        assert response.status_code == 401
        assert "Token de autenticação inválido" in response.json()["detail"]
    
    def test_cleanup_with_invalid_token_returns_401(self):
        """Deve retornar 401 com token inválido"""
        response = client.post(
            "/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=50",
            headers={"X-Scheduler-Token": "invalid-token"}
        )
        assert response.status_code == 401
        assert "Token de autenticação inválido" in response.json()["detail"]
    
    @pytest.mark.skipif(
        not settings.scheduler_token,
        reason="SCHEDULER_TOKEN not configured"
    )
    def test_cleanup_with_valid_token_returns_200(self):
        """Deve retornar 200 com token válido e dry_run=true"""
        response = client.post(
            "/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=50",
            headers={"X-Scheduler-Token": settings.scheduler_token}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        assert "found" in data
        assert "deleted" in data
        assert "orders" in data


class TestCleanupEndpointValidation:
    """Testes de validação de parâmetros"""
    
    @pytest.mark.skipif(
        not settings.scheduler_token,
        reason="SCHEDULER_TOKEN not configured"
    )
    def test_cleanup_limit_validation_min(self):
        """Deve validar que limit >= 1"""
        response = client.post(
            "/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=0",
            headers={"X-Scheduler-Token": settings.scheduler_token}
        )
        assert response.status_code == 422  # Unprocessable Entity
    
    @pytest.mark.skipif(
        not settings.scheduler_token,
        reason="SCHEDULER_TOKEN not configured"
    )
    def test_cleanup_limit_validation_max(self):
        """Deve validar que limit <= 1000"""
        response = client.post(
            "/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=1001",
            headers={"X-Scheduler-Token": settings.scheduler_token}
        )
        assert response.status_code == 422  # Unprocessable Entity
    
    @pytest.mark.skipif(
        not settings.scheduler_token,
        reason="SCHEDULER_TOKEN not configured"
    )
    def test_cleanup_default_parameters(self):
        """Deve usar valores padrão (dry_run=false, limit=200)"""
        response = client.post(
            "/internal/maintenance/cleanup-expired-orders",
            headers={"X-Scheduler-Token": settings.scheduler_token}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is False


class TestCleanupEndpointResponse:
    """Testes do formato de resposta"""
    
    @pytest.mark.skipif(
        not settings.scheduler_token,
        reason="SCHEDULER_TOKEN not configured"
    )
    def test_dry_run_response_format(self):
        """Deve retornar formato correto em dry_run"""
        response = client.post(
            "/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=50",
            headers={"X-Scheduler-Token": settings.scheduler_token}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Estrutura esperada
        assert "dry_run" in data
        assert "found" in data
        assert "deleted" in data
        assert "orders" in data
        
        # Valores esperados
        assert data["dry_run"] is True
        assert data["deleted"] == 0
        assert isinstance(data["found"], int)
        assert isinstance(data["orders"], list)
    
    @pytest.mark.skipif(
        not settings.scheduler_token,
        reason="SCHEDULER_TOKEN not configured"
    )
    def test_production_run_response_format(self):
        """Deve retornar formato correto sem dry_run"""
        response = client.post(
            "/internal/maintenance/cleanup-expired-orders?dry_run=false&limit=50",
            headers={"X-Scheduler-Token": settings.scheduler_token}
        )
        assert response.status_code == 200
        data = response.json()
        
        # Estrutura esperada (sem campo 'orders')
        assert "dry_run" in data
        assert "found" in data
        assert "deleted" in data
        assert data["dry_run"] is False
        assert isinstance(data["found"], int)
        assert isinstance(data["deleted"], int)


class TestCleanupEndpointIntegration:
    """Testes de integração com Firestore"""
    
    @pytest.mark.skipif(
        not settings.firestore_enabled,
        reason="Firestore not enabled"
    )
    @pytest.mark.skipif(
        not settings.scheduler_token,
        reason="SCHEDULER_TOKEN not configured"
    )
    def test_cleanup_can_connect_to_firestore(self):
        """Deve conseguir conectar ao Firestore sem erro"""
        response = client.post(
            "/internal/maintenance/cleanup-expired-orders?dry_run=true&limit=1",
            headers={"X-Scheduler-Token": settings.scheduler_token}
        )
        # 200 = sucesso, 400 = firestore desabilitado, 500 = erro
        assert response.status_code in [200, 400]


# ============================================================================
# Testes para endpoint de saúde (health check)
# ============================================================================

class TestHealthEndpoint:
    """Testes do health check"""
    
    def test_health_returns_200(self):
        """Deve retornar 200 no health check"""
        response = client.get("/health")
        assert response.status_code == 200
    
    def test_health_response_format(self):
        """Deve retornar formato correto"""
        response = client.get("/health")
        data = response.json()
        assert "status" in data
        assert data["status"] == "ok"
        assert "app_name" in data


# ============================================================================
# Testes para endpoints de catalog
# ============================================================================

class TestCatalogEndpoint:
    """Testes dos endpoints de catálogo"""
    
    def test_catalog_endpoint_exists(self):
        """Deve retornar resposta válida para catálogo"""
        response = client.post("/catalog/list")
        # Pode retornar 400 se não houver dados, mas não 404
        assert response.status_code != 404


# ============================================================================
# Manual test guide
# ============================================================================

if __name__ == "__main__":
    print("""
    ╔════════════════════════════════════════════════════════════════╗
    ║        Testes para Diotex Tecidos - Cleanup Scheduler         ║
    ╚════════════════════════════════════════════════════════════════╝
    
    Para rodar os testes, execute:
    
    1. Todos os testes:
       pytest tests/test_cleanup_endpoint.py -v
    
    2. Apenas testes de autenticação:
       pytest tests/test_cleanup_endpoint.py::TestCleanupEndpointAuthentication -v
    
    3. Apenas testes com Firestore:
       pytest tests/test_cleanup_endpoint.py -v -m "not skip"
    
    4. Com cobertura:
       pytest tests/test_cleanup_endpoint.py --cov=app --cov-report=html
    
    5. Executar um teste específico:
       pytest tests/test_cleanup_endpoint.py::TestCleanupEndpointAuthentication::test_cleanup_without_token_returns_401 -v
    
    ╔════════════════════════════════════════════════════════════════╗
    ║                    Requisitos de Configuração                 ║
    ╚════════════════════════════════════════════════════════════════╝
    
    Certifique-se de ter:
    - SCHEDULER_TOKEN definido em .env
    - FIRESTORE_ENABLED=true em .env (se quiser testar integração)
    - FastAPI e pytest instalados
    
    Instale dependências de teste:
       pip install pytest pytest-cov pytest-asyncio httpx
    """)
