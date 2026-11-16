"""
Motor de autorización (Persona 3).

  matriz.py   la matriz (78 celdas) y el vocabulario de acciones, como datos
  reglas.py   la decisión (PDP): regla de decisión, RG1, RG3, RG5, RG6, Cx
  app.py      el PDP como servicio HTTP (FastAPI), sin acceso a la base
  pep.py      lado Django: arma los atributos leyendo la base en cada decisión
  cliente.py  lado Django: gancho autorizar() que consulta al servicio

matriz.py, reglas.py y roles.py son Python puro: el contenedor del autorizador
solo lleva este paquete y no importa Django. La frontera de confianza es la red
(Cloud Run privado con token OIDC).
"""
