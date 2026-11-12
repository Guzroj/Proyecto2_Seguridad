"""
Motor de autorización (Persona 3).

Servicio HTTP independiente de Django. Django lo consulta a través de
autorizacion.cliente (ganchos reales) y el motor decide rol, alcance ABAC,
MFA y HBAC. La frontera de confianza es la red: Cloud Run privado
(--no-allow-unauthenticated) valida el token OIDC del llamador.
"""
