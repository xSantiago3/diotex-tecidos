#!/usr/bin/env python3
"""
Script interativo para testar o endpoint de limpeza de pedidos expirados
Execute com: python3 scripts/test_cleanup_interactive.py
"""

import requests
import json
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# Configuração
API_URL = os.getenv("API_URL", "http://localhost:8000")
SCHEDULER_TOKEN = os.getenv("SCHEDULER_TOKEN", "")
CLEANUP_ENDPOINT = f"{API_URL}/internal/maintenance/cleanup-expired-orders"

# Cores para terminal
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"


def print_header(text):
    print(f"\n{BLUE}{BOLD}{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}{RESET}\n")


def print_success(text):
    print(f"{GREEN}✓ {text}{RESET}")


def print_error(text):
    print(f"{RED}✗ {text}{RESET}")


def print_warning(text):
    print(f"{YELLOW}⚠ {text}{RESET}")


def print_info(text):
    print(f"{BLUE}ℹ {text}{RESET}")


def test_health_check():
    """Testa se a API está respondendo"""
    print_header("Test 1: Health Check")
    
    try:
        response = requests.get(f"{API_URL}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print_success(f"API está online")
            print(f"  Status: {data.get('status')}")
            print(f"  App: {data.get('app_name')}")
            return True
        else:
            print_error(f"API retornou status {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print_error(f"Não conseguiu conectar em {API_URL}")
        print_warning("Certifique-se de que a API está rodando")
        return False
    except Exception as e:
        print_error(f"Erro: {str(e)}")
        return False


def test_auth_required():
    """Testa se autenticação é obrigatória"""
    print_header("Test 2: Authentication Required")
    
    try:
        # Sem token
        response = requests.post(
            f"{CLEANUP_ENDPOINT}?dry_run=true&limit=50",
            timeout=5
        )
        if response.status_code == 401:
            print_success("Autenticação é obrigatória (401)")
            data = response.json()
            print(f"  Mensagem: {data.get('detail', 'N/A')}")
            return True
        else:
            print_error(f"Esperava 401, recebeu {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Erro: {str(e)}")
        return False


def test_invalid_token():
    """Testa rejeição de token inválido"""
    print_header("Test 3: Invalid Token Rejection")
    
    try:
        response = requests.post(
            f"{CLEANUP_ENDPOINT}?dry_run=true&limit=50",
            headers={"X-Scheduler-Token": "invalid-token-12345"},
            timeout=5
        )
        if response.status_code == 401:
            print_success("Token inválido foi rejeitado (401)")
            return True
        else:
            print_error(f"Esperava 401, recebeu {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Erro: {str(e)}")
        return False


def test_dry_run_with_valid_token():
    """Testa cleanup em modo dry_run (sem fazer alterações)"""
    print_header("Test 4: Dry Run (Read-Only)")
    
    if not SCHEDULER_TOKEN:
        print_warning("SCHEDULER_TOKEN não configurado em .env")
        return False
    
    try:
        response = requests.post(
            f"{CLEANUP_ENDPOINT}?dry_run=true&limit=50",
            headers={"X-Scheduler-Token": SCHEDULER_TOKEN},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            print_success("Cleanup em dry_run executado com sucesso")
            print(f"  Pedidos encontrados: {data.get('found', 0)}")
            print(f"  Pedidos que seriam deletados: 0 (dry_run=true)")
            print(f"  Pedidos deletados (real): {data.get('deleted', 0)}")
            
            # Mostrar detalhes dos pedidos
            orders = data.get('orders', [])
            if orders:
                print(f"\n  Detalhes dos {len(orders)} primeiro(s) pedido(s):")
                for i, order in enumerate(orders[:3], 1):
                    print(f"    {i}. ID: {order.get('order_id')}, " +
                          f"Status: {order.get('status')}, " +
                          f"Expira em: {order.get('expires_at', 'N/A')}")
            else:
                print_info("  Nenhum pedido expirado encontrado")
            
            return True
        else:
            print_error(f"Retornou status {response.status_code}")
            print(f"  Resposta: {response.text}")
            return False
    except Exception as e:
        print_error(f"Erro: {str(e)}")
        return False


def test_production_run():
    """Testa cleanup em modo real (com deleção)"""
    print_header("Test 5: Production Run (Delete Expired Orders)")
    
    if not SCHEDULER_TOKEN:
        print_warning("SCHEDULER_TOKEN não configurado em .env")
        return False
    
    print_warning("Este teste vai DELETAR pedidos expirados!")
    response_input = input("Deseja continuar? (s/n): ").strip().lower()
    
    if response_input != 's':
        print_info("Teste cancelado")
        return False
    
    try:
        response = requests.post(
            f"{CLEANUP_ENDPOINT}?dry_run=false&limit=200",
            headers={"X-Scheduler-Token": SCHEDULER_TOKEN},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            print_success("Cleanup em produção executado com sucesso")
            print(f"  Pedidos encontrados: {data.get('found', 0)}")
            print(f"  Pedidos deletados: {data.get('deleted', 0)}")
            return True
        else:
            print_error(f"Retornou status {response.status_code}")
            print(f"  Resposta: {response.text}")
            return False
    except Exception as e:
        print_error(f"Erro: {str(e)}")
        return False


def test_parameter_validation():
    """Testa validação de parâmetros"""
    print_header("Test 6: Parameter Validation")
    
    if not SCHEDULER_TOKEN:
        print_warning("SCHEDULER_TOKEN não configurado em .env")
        return False
    
    all_passed = True
    
    # Teste limit < 1
    try:
        response = requests.post(
            f"{CLEANUP_ENDPOINT}?dry_run=true&limit=0",
            headers={"X-Scheduler-Token": SCHEDULER_TOKEN},
            timeout=5
        )
        if response.status_code == 422:
            print_success("Validação: limit=0 rejeitado (422)")
        else:
            print_warning(f"Validação: limit=0 retornou {response.status_code}")
            all_passed = False
    except Exception as e:
        print_error(f"Erro no teste limit=0: {str(e)}")
        all_passed = False
    
    # Teste limit > 1000
    try:
        response = requests.post(
            f"{CLEANUP_ENDPOINT}?dry_run=true&limit=1001",
            headers={"X-Scheduler-Token": SCHEDULER_TOKEN},
            timeout=5
        )
        if response.status_code == 422:
            print_success("Validação: limit=1001 rejeitado (422)")
        else:
            print_warning(f"Validação: limit=1001 retornou {response.status_code}")
            all_passed = False
    except Exception as e:
        print_error(f"Erro no teste limit=1001: {str(e)}")
        all_passed = False
    
    return all_passed


def test_defaults():
    """Testa valores padrão"""
    print_header("Test 7: Default Parameters")
    
    if not SCHEDULER_TOKEN:
        print_warning("SCHEDULER_TOKEN não configurado em .env")
        return False
    
    try:
        response = requests.post(
            CLEANUP_ENDPOINT,
            headers={"X-Scheduler-Token": SCHEDULER_TOKEN},
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            print_success("Defaults funcionam corretamente")
            print(f"  dry_run (padrão false): {data.get('dry_run')}")
            print(f"  Pedidos encontrados (limit=200): {data.get('found', 0)}")
            return True
        else:
            print_error(f"Retornou status {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Erro: {str(e)}")
        return False


def main():
    """Função principal"""
    print(f"\n{BOLD}{BLUE}")
    print("╔════════════════════════════════════════════════════════════╗")
    print("║    Testes Interativos - Cleanup Scheduler Endpoint        ║")
    print("║    Diotex Tecidos Commerce Backend                        ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print(f"{RESET}\n")
    
    print_info(f"API URL: {API_URL}")
    print_info(f"Endpoint: {CLEANUP_ENDPOINT}")
    if SCHEDULER_TOKEN:
        print_success(f"SCHEDULER_TOKEN: {SCHEDULER_TOKEN[:20]}...")
    else:
        print_warning("SCHEDULER_TOKEN: não configurado")
    
    print("\n")
    
    # Executar testes
    results = {
        "Health Check": test_health_check(),
        "Auth Required": test_auth_required(),
        "Invalid Token": test_invalid_token(),
        "Dry Run": test_dry_run_with_valid_token(),
        "Production Run": test_production_run(),
        "Parameter Validation": test_parameter_validation(),
        "Default Parameters": test_defaults(),
    }
    
    # Resumo
    print_header("Test Summary")
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
        print(f"  {test_name}: {status}")
    
    print(f"\nResultado: {passed}/{total} testes passaram")
    
    if passed == total:
        print_success("Todos os testes passaram! ✓")
    elif passed >= total * 0.7:
        print_warning(f"Maioria dos testes passou ({passed}/{total})")
    else:
        print_error(f"Muitos testes falharam ({total - passed} falhas)")
    
    print(f"\n{BLUE}{'='*60}{RESET}\n")


if __name__ == "__main__":
    main()
