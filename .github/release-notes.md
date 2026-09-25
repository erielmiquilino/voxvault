## Qual arquivo baixar

**`VoxVault-X.Y.Z-setup.exe`** é o instalador para Windows 10 e 11 de 64 bits.
Ele instala só para o seu usuário, sem pedir administrador, e não exige nada
instalado antes: nem Python, nem uv, nem ffmpeg. Para conferir o download,
compare a soma com `SHA256SUMS.txt`:

```powershell
Get-FileHash .\VoxVault-X.Y.Z-setup.exe -Algorithm SHA256
```

## Aviso: executável não assinado

O instalador não tem assinatura de código, então o Windows SmartScreen avisa na
primeira execução. Clique em **Mais informações → Executar assim mesmo**.
Antivírus às vezes estranham o preparo do primeiro uso, que baixa e executa um
interpretador Python; se o seu bloquear, a tela de preparo diz o que parou e
permite retomar. E os que protegem o acesso ao microfone, como o Kaspersky,
seguram o áudio do VoxVault até você permitir no aviso deles, na primeira
gravação.

## O que é baixado no primeiro uso

Nada é baixado antes de você ver esta lista na tela de preparo e confirmar:

| Parte | Download | Quando |
|---|---|---|
| Interpretador Python 3.12 | 21 MB | sempre |
| Dependências do núcleo | 96 MB | sempre |
| Aceleração por GPU (CUDA) | 1,3 GB | só com GPU NVIDIA |
| Modelo `large-v3` | 2,9 GB | GPU com 5600 MB de memória ou mais |
| Modelo `large-v3-turbo` | 1,5 GB | GPU menor, ou sem GPU |

No total, cerca de 4,3 GB com GPU NVIDIA de 8 GB e 1,6 GB sem GPU. Um preparo
interrompido continua de onde parou.

## Requisitos

- Windows 10 ou 11, 64 bits.
- Cerca de 2 GB livres sem GPU, ou 5,5 GB com GPU NVIDIA, mais cerca de 120 MB
  por hora de reunião gravada.
- GPU NVIDIA opcional. Sem ela, a transcrição roda na CPU a cerca de 1,4 vez o
  tempo real num processador de 6 núcleos: uma reunião de 1 h leva por volta
  de 45 min.
- Internet só para o preparo.

## O que o VoxVault acessa na sua máquina

- **Rede:** só no preparo e quando você pede outro modelo, e só estes
  destinos: `releases.astral.sh` (interpretador), `pypi.org` e
  `files.pythonhosted.org` (pacotes) e `huggingface.co`, com a CDN dele em
  `*.hf.co` (modelo). Sem telemetria, sem relatório de falha, sem procurar
  versões novas. Depois do preparo, tudo funciona sem rede.
- **Áudio:** o microfone e a saída de áudio escolhidos, só enquanto uma
  gravação está em andamento.
- **Detecção de reunião:** qual processo está usando o microfone, pelo mesmo
  registro que o Windows usa para o indicador de microfone em uso. Não lê
  áudio, título de janela nem conteúdo.
- **Disco:** `%LOCALAPPDATA%\VoxVault` (o aplicativo), `%USERPROFILE%\.voxvault`
  (configuração e ambiente) e a pasta de dados que você escolher, por padrão
  `%USERPROFILE%\VoxVault`.
- **Início com o Windows:** só se você ligar, em Configurações.
- **`PATH` do seu usuário:** o preparo acrescenta, no fim, a pasta
  `%USERPROFILE%\.voxvault\bin`, só com `voxvault` e `voxvault-mcp`, para a
  linha de comando ser chamada pelo nome. O Python do ambiente não entra no
  `PATH`.
- **Endereço `voxvault:`:** registrado para o seu usuário em
  `HKCU\Software\Classes`, para que clicar numa notificação abra a reunião. Um
  endereço só abre a janela; gravar, só pelo botão "Gravar" da própria
  notificação.

A desinstalação remove o aplicativo, `%USERPROFILE%\.voxvault`, o endereço
`voxvault:`, a pasta do VoxVault no `PATH` e o início com o Windows, e mantém a
pasta de dados intacta.
