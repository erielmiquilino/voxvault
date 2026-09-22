# Revisão externa das specs da v1 — Codex (gpt-6-astra, effort xhigh)

Data: 2026-09-21 · Escopo: DESIGN.md e as 5 changes OpenSpec · Somente leitura.

1. **[CRÍTICO] A fase 0 exige intervenção humana por definição.**  
   **Arquivo/requisito:** [setup-transcription-benchmark/tasks.md](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/tasks.md:43), tarefas 5.1–5.4; “Medição e portão de decisão”.

   Nenhum artefato identifica qual gravação real usar, e a tarefa 5.4 exige leitura comparativa **com o usuário** antes de escolher o motor. Um agente não consegue cumprir essa etapa autonomamente sem inventar uma aprovação.

   **Especificar:** a entrada autorizada para o benchmark e a decisão qualitativa previamente tomada, ou uma escolha local provisória que permita implementar as fases seguintes enquanto a avaliação humana permanece pendente.

2. **[CRÍTICO] O algoritmo de alinhamento pode inserir silêncio onde não houve perda de áudio.**  
   **Arquivos/requisitos:** [audio-capture/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:28), “Alinhamento temporal”; [design do núcleo](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/design.md:32), carimbo por callback.

   O design manda posicionar o bloco pelo instante em que o callback o recebe, confundindo atraso de entrega com instante de captura; o próprio PortAudio distingue esses dois tempos. Sob carga, isso pode fabricar lacunas, e completar os arquivos até durações iguais não demonstra que as falas estejam alinhadas. [Referência do PortAudio](https://portaudio.com/docs/v19-doxydocs/structPaStreamCallbackTimeInfo.html).

   **Especificar:** qual timestamp representa a primeira amostra, como os dois streams são convertidos para uma referência comum e como tratar latência, timestamps inválidos, sobreposição e deriva. A aceitação precisa medir o deslocamento de eventos conhecidos no áudio, com tolerância numérica, não apenas comparar durações.

3. **[CRÍTICO] A troca de dispositivo pode deixar a gravação presa numa saída silenciosa.**  
   **Arquivos/requisitos:** [audio-capture/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:77), “Seleção de dispositivos” e “Resiliência”; [settings-ui/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/settings-ui/spec.md:7), “Configuração de dispositivos”.

   Conectar um fone pode mudar o padrão mantendo o dispositivo anterior disponível; esperar erro ou indisponibilidade do stream não cobre esse caso. “Padrão do sistema” também não determina qual papel usar — multimídia ou comunicações — e a seleção fixada entra em conflito com a regra de migrar sempre para o novo padrão. O Windows oferece notificação específica de mudança de padrão por papel. [Documentação da Microsoft](https://learn.microsoft.com/en-us/windows/win32/api/mmdeviceapi/nf-mmdeviceapi-immnotificationclient-ondefaultdevicechanged).

   **Especificar:** papel escolhido, identidade persistente dos dispositivos e reação a mudanças de padrão sem desconexão. Definir separadamente a política para “seguir padrão” e “dispositivo fixado”, incluindo reconexão, mudança de formato e prazo máximo de recuperação.

4. **[CRÍTICO] Falhas de escrita durante a reunião não têm comportamento definido.**  
   **Arquivos/requisitos:** [audio-capture/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:119), “Escrita incremental”; [recording-session/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/recording-session/spec.md:101), “Recuperação”; [environment-check/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/specs/environment-check/spec.md:48), espaço disponível.

   Há aviso de pouco espaço e monitoramento da fila, mas nenhum requisito para disco cheio, escrita recusada ou fila que continua crescendo porque o escritor parou. Também não está definido o que acontece se a compressão falhar ou o processo morrer entre finalizar o áudio, atualizar os metadados e enfileirar a sessão.

   **Especificar:** limite da fila, tratamento explícito de blocos perdidos e encerramento recuperável quando não for possível continuar escrevendo. Definir o ponto de durabilidade, a perda máxima admitida e uma finalização retomável que preserve o áudio original até validar o comprimido e recuperar o enfileiramento.

5. **[CRÍTICO] Falta definir quem mantém captura e fila vivas entre os comandos e o app.**  
   **Arquivos/requisitos:** [tarefas do núcleo](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/tasks.md:76), CLI; [desktop-shell/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/desktop-shell/spec.md:33), “Ciclo de vida”; [tarefas do desktop](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/tasks.md:74), tarefa 6.7.

   `record start`, `pause` e `stop` são invocações distintas, mas não existe contrato para o processo residente nem para seu controle antes da API da fase 3. Fechar o app deve encerrar o núcleo, enquanto a tarefa 6.7 espera encontrar a reunião transcrita sem determinar se o fechamento aguarda isso ou se exige reabertura. No Windows, terminar um processo Python não encerra automaticamente seus descendentes. [Documentação do Python](https://docs.python.org/3.12/library/multiprocessing.html#multiprocessing.Process.terminate).

   **Especificar:** proprietário único da captura e da fila, comunicação da CLI com ele e convivência com o Tauri. Determinar encerramento de toda a árvore de processos, destino das transcrições pendentes e comportamento após queda durante gravação ou suspensão/retomada do Windows.

6. **[IMPORTANTE] A promessa de conteúdo das trilhas excede o que o loopback entrega.**  
   **Arquivo/requisito:** [audio-capture/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:7), “Captura simultânea em trilhas separadas”.

   O cenário exige que `system` contenha apenas a plataforma da reunião, mas loopback do endpoint captura sua mistura de áudio, incluindo outros aplicativos. Portanto, um vídeo paralelo — ou a reprodução de uma reunião antiga pelo próprio VoxVault — pode entrar na gravação como fala dos participantes; se a reunião estiver em outra saída, pode não entrar. [Documentação do WASAPI](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording).

   **Especificar:** que a atribuição representa a origem física, com captura de todo o áudio do endpoint selecionado e necessidade de ele corresponder à saída da reunião. Definir o comportamento da reprodução do próprio VoxVault durante uma gravação e limitar os cenários de aceite a essas condições.

7. **[IMPORTANTE] A degradação para CPU conflita com o diagnóstico obrigatório.**  
   **Arquivos/requisitos:** [environment-check/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/specs/environment-check/spec.md:22), biblioteca ausente e bloqueio antecipado; [transcription-engine/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/specs/transcription-engine/spec.md:105), “Execução acelerada com degradação explícita”.

   GPU presente com DLL ausente gera `falha`, mas aceleração indisponível exige continuar em CPU; não está estabelecido qual regra prevalece. Instalar os pacotes tampouco define como as DLLs serão encontradas pelo worker iniciado pelo Tauri, nem qual precisão substituirá `float16` na CPU; a NVIDIA distingue instalação dos pacotes e preparação do ambiente hospedeiro. [Documentação da NVIDIA](https://docs.nvidia.com/deeplearning/cudnn/installation/latest/windows.html).

   **Especificar:** uma matriz para GPU ausente, runtime quebrado e memória insuficiente, distinguindo fallback, bloqueio da transcrição e disponibilidade da gravação. Fixar a configuração suportada de CPU e exigir validação de inferência no mesmo ambiente de lançamento usado pelo app, com versões e resolução de DLLs reproduzíveis.

8. **[IMPORTANTE] O MCP introduz vários escritores, mas o contrato continua tratando apenas leitores concorrentes.**  
   **Arquivos/requisitos:** [transcript-store/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/transcript-store/spec.md:94), “Acesso concorrente”; [mcp-server/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-mcp-transcript-server/specs/mcp-server/spec.md:7), “Operação independente”.

   Núcleo, Claude Desktop e Codex podem escrever simultaneamente, inclusive iniciar migrações na abertura. WAL permite leitores concorrentes, mas continua admitindo apenas um escritor por vez e pode retornar `SQLITE_BUSY`; a promessa irrestrita de não bloquear nenhum lado não estabelece um comportamento implementável para contenção. [Documentação do SQLite](https://www.sqlite.org/wal.html).

   **Especificar:** transações curtas, espera/repetição com prazo definido, resposta quando o prazo expirar e serialização das migrações. A aceitação precisa incluir gravação de notas pelos dois clientes enquanto o núcleo publica uma transcrição, sem perda nem bloqueio do callback de captura.

9. **[IMPORTANTE] Reprocessar pode misturar resultados de motores diferentes ou destruir a única transcrição utilizável.**  
   **Arquivos/requisitos:** [transcription-pipeline/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/transcription-pipeline/spec.md:65), falha por trilha e reprocessamento; [tarefas do núcleo](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/tasks.md:8), substituição atômica por trilha.

   A atomicidade exigida é por trilha, mas o reprocessamento promete substituir a reunião inteira. Se o microfone novo for persistido e a trilha de sistema falhar, não está dito se os segmentos antigos do sistema permanecem, desaparecem ou tornam a nova tentativa inválida; também falta definir o resultado quando ambas falham.

   **Especificar:** política de preservação do resultado anterior e momento de publicação de uma nova revisão, inclusive quando parcial. Vincular segmentos, identificação do motor, FTS e exportações à revisão publicada, com recuperação de falhas entre banco e arquivos.

10. **[IMPORTANTE] A troca do diretório de dados pode separar silenciosamente o histórico visto pelo app e pelos agentes.**  
    **Arquivos/requisitos:** [settings-ui/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/settings-ui/spec.md:52), “Configuração do diretório de dados”; [design da fase 0](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/design.md:64), resolução por configuração.

    Não existe fonte persistente comum de configuração nem precedência entre arquivo, ambiente e escolhas da UI. Ao mudar o diretório com Claude ou Codex abertos, seus servidores podem continuar ligados ao banco anterior, enquanto novas reuniões aparecem apenas no app; o destino das filas pendentes no diretório antigo também fica indeterminado.

    **Especificar:** localização e precedência da configuração compartilhada, independentes do diretório corrente de cada processo. Determinar quando a mudança passa a valer, como os MCPs são atualizados ou exigem reinício e o que acontece com filas e histórico do diretório anterior.

11. **[IMPORTANTE] O MCP permite editar notas que não oferece uma forma definida de recuperar integralmente.**  
    **Arquivo/requisito:** [mcp-server/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-mcp-transcript-server/specs/mcp-server/spec.md:26), leitura, busca e fronteira de escrita.

    A listagem devolve contagem de notas; a leitura devolve segmentos; a busca devolve recortes. Nenhuma ferramenta é obrigada a listar identificadores de notas e recuperar seu conteúdo completo, embora atualização e remoção dependam desses identificadores.

    **Especificar:** leitura paginada das notas de uma reunião e obtenção integral de uma nota por identificador, com tipo, autoria e datas. Definir também os identificadores retornados pela criação e pela busca.

12. **[IMPORTANTE] A paginação temporal não garante ausência de duplicação nem respeito ao limite.**  
    **Arquivos/requisitos:** [mcp-server/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-mcp-transcript-server/specs/mcp-server/spec.md:45), recorte e paginação; [tarefas do MCP](E:/projetosAleatorios/VoxVault/openspec/changes/add-mcp-transcript-server/tasks.md:28), tarefas 3.3–3.4.

    O recorte inclui segmentos que se sobrepõem ao intervalo, portanto um segmento atravessando a fronteira aparece em duas páginas adjacentes. Paginar apenas pelo próximo timestamp também pode perder segmentos com início idêntico, e uma única janela pode exceder o teto de resposta.

    **Especificar:** ordenação total com desempate e cursor que assegure avanço sem perda ou duplicação, distinguindo paginação de consulta por sobreposição. Definir unidade e valor do teto, tratamento de um único item excessivo e validade do cursor após reprocessamento.

13. **[IMPORTANTE] Os principais critérios de aceite deixam o implementador escolher o próprio resultado aprovado.**  
    **Arquivos/requisitos:** [desktop-shell/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/desktop-shell/spec.md:129), “Custo desprezível”; [transcription-pipeline/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/transcription-pipeline/spec.md:37), início sem atraso perceptível; [audio-capture/spec.md](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:28), tolerância temporal.

    “Limites configurados”, “tolerância configurável” e “sem atraso perceptível” não possuem valores de referência. Qualquer implementação pode passar elevando seus próprios limites, inclusive uma que consuma CPU demais durante a reunião ou perca seus primeiros segundos.

    **Especificar:** valores padrão e tetos de aprovação para CPU, memória, atraso até a primeira amostra e erro de alinhamento. Determinar quais processos entram na medição — incluindo WebView2 e descendentes —, como agregar as amostras e quais condições de carga compõem o ensaio.

## Veredito

**Não. A v1 não está especificada para implementação autônoma de ponta a ponta.** A fase 0 exige uma decisão humana, e os contratos de alinhamento, seleção de dispositivos, persistência e ciclo de vida deixam abertas decisões que podem perder áudio irreversivelmente.

