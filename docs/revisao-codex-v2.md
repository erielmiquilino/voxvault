# Revisão externa das specs da v1 — Codex (gpt-6-astra, effort xhigh) — rodada 2

Data: 2026-09-21 · Verificação das 13 correções da rodada 1 e novos apontamentos.

**Parte 1 — Verificação das correções**

1. **RESOLVIDO.** O [motor provisório](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/specs/transcription-engine/spec.md:141) e a declaração de que nenhuma tarefa de avaliação bloqueia as fases seguintes em [tasks.md:53](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/tasks.md:53) resolvem o desbloqueio pedido.

2. **PARCIAL.** [Alinhamento temporal](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:56) distingue aquisição de entrega e fixa tolerância, mas ainda faltam a conversão concreta dos relógios, o tratamento de posições sobrepostas e a inicialização sem referência válida; há também o impedimento técnico descrito na Parte 2.1.

3. **PARCIAL.** [Seleção e reação a dispositivos](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:152) definem papéis, identidade e políticas, mas os 5 segundos limitam apenas a **detecção**: faltam prazo de recuperação e destino da trilha quando “seguir padrão” não encontra uma saída utilizável.

4. **PARCIAL.** [Falhas de escrita](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:222) e [finalização retomável](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/recording-session/spec.md:71) foram especificadas, mas a garantia de perda máxima contradiz o tamanho permitido da fila, conforme Parte 2.2.

5. **PARCIAL.** O [serviço residente](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/recording-session/spec.md:7) resolve propriedade e sobrevivência entre comandos, mas falta estabelecer como CLI e Tauri descobrem e acessam a mesma instância, inclusive o segredo que [tasks.md:7](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/tasks.md:7) manda publicar apenas para o hospedeiro.

6. **PARCIAL.** A [captura da mistura do endpoint](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:15) e o bloqueio de reprodução estão definidos, mas [DESIGN.md:23](E:/projetosAleatorios/VoxVault/DESIGN.md:23) e [library-ui/spec.md:80](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/library-ui/spec.md:80) ainda prometem conteúdo exclusivo de cada lado; essas promessas precisam ser corrigidas.

7. **RESOLVIDO.** A [matriz por capacidade](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/specs/environment-check/spec.md:44), o [CPU em `int8`](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/specs/transcription-engine/spec.md:105) e a inferência diagnóstica no ambiente real de lançamento eliminam as políticas concorrentes.

8. **RESOLVIDO.** [Acesso concorrente e evolução do esquema](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/transcript-store/spec.md:149) agora estabelecem transações curtas, espera de 5 segundos, migrações serializadas com prazo de 30 segundos e isolamento do caminho de captura.

9. **PARCIAL.** [Revisões de transcrição](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/transcript-store/spec.md:29) resolvem preservação e publicação no banco, mas ainda falta recuperar uma queda entre publicar a revisão e substituir os arquivos exportados; exigir que ambos reflitam “sempre” a revisão ativa não define essa recuperação.

10. **PARCIAL.** [Configuração compartilhada](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/specs/environment-check/spec.md:91) e [troca de diretório](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/settings-ui/spec.md:62) cobrem persistência, fila e reinício do MCP, mas não resolvem a aplicação efetiva da mudança ao serviço nem os overrides de maior precedência, conforme Parte 2.4.

11. **RESOLVIDO.** [Leitura de notas](E:/projetosAleatorios/VoxVault/openspec/changes/add-mcp-transcript-server/specs/mcp-server/spec.md:131) oferece listagem com metadados, leitura integral e identificadores retornados pela criação e pela busca.

12. **RESOLVIDO.** [Paginação e limites](E:/projetosAleatorios/VoxVault/openspec/changes/add-mcp-transcript-server/specs/mcp-server/spec.md:68) separam recorte de paginação, estabelecem desempate, teto numérico, tratamento de segmento excessivo e invalidação pela revisão.

13. **PARCIAL.** O [orçamento de recursos](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/desktop-shell/spec.md:146) agora tem números, processos e amostragem definidos, mas os critérios de início divergem entre 1.500 e 2.000 ms e o ensaio ainda não fixa as condições de carga concorrente.

**Parte 2 — Problemas novos, em ordem de prioridade**

1. **[CRÍTICO] O backend escolhido não fornece o timestamp que a correção passou a exigir.**  
   Arquivos/requisito: [design do núcleo:26](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/design.md:26) e [audio-capture — Alinhamento temporal](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:56).  
   No código consultado do PyAudioWPatch, `GetBuffer` recebe `NULL` para posição e timestamp, enquanto `inputBufferAdcTime` é calculado como relógio corrente mais tempo pendente — portanto, ler esse campo não entrega automaticamente o instante real de aquisição. [Fonte do backend](https://raw.githubusercontent.com/s0d3s/PyAudioWPatch/master/portaudio_v19/src/hostapi/wasapi/pa_win_wasapi.c). É necessário escolher uma integração que exponha os timestamps reais do WASAPI e validar isso antes de construir o alinhamento sobre ela.

2. **[CRÍTICO] A fila permitida torna impossível garantir perda máxima de 5 segundos.**  
   Arquivo/requisito: [audio-capture — Escrita incremental](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:222).  
   Uma queda com 40 segundos acumulados e ainda não escritos perde esses 40 segundos, embora a spec permita continuar até ultrapassar 60 e prometa perder no máximo 5. É preciso compatibilizar o limite de áudio não durável com a garantia de recuperação, incluindo o comportamento quando o escritor para de avançar.

3. **[CRÍTICO] Instância única por diretório não garante exclusividade de gravação nem de GPU.**  
   Arquivos/requisitos: [recording-session — Serviço residente e sessão exclusiva](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/recording-session/spec.md:13) e [transcription-pipeline — Não concorrência](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/transcription-pipeline/spec.md:23).  
   Dois comandos com diretórios distintos, possibilidade autorizada pela configuração, podem criar dois serviços: ambos gravando, ou um transcrevendo enquanto o outro grava. É necessário definir o escopo global da exclusividade e da arbitragem de recursos, ou proibir explicitamente serviços simultâneos para diretórios diferentes.

4. **[IMPORTANTE] A UI pode aceitar uma troca de diretório que nunca passa a valer.**  
   Arquivos/requisitos: [environment-check — Precedência](E:/projetosAleatorios/VoxVault/openspec/changes/setup-transcription-benchmark/specs/environment-check/spec.md:91) e [settings-ui — Diretório de dados](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/settings-ui/spec.md:62).  
   Se serviço ou MCP receberam o caminho por argumento ou ambiente, escrever outro caminho no arquivo não altera o valor efetivo; reiniciar o MCP com o mesmo override também não resolve. Deve ser especificado como a UI trata campos sobrepostos e como ocorre a transição do serviço, das conexões e da identidade da instância para o novo armazenamento.

5. **[IMPORTANTE] O estado da tentativa passou a competir com a disponibilidade da revisão publicada.**  
   Arquivos/requisitos: [transcript-store — Revisão em construção](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/transcript-store/spec.md:75), [transcription-pipeline — Estado observável](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/transcription-pipeline/spec.md:94) e [mcp-server — Reunião na fila](E:/projetosAleatorios/VoxVault/openspec/changes/add-mcp-transcript-server/specs/mcp-server/spec.md:50).  
   Durante reprocessamento, uma reunião pode ter transcrição completa publicada e estar “aguardando”, “transcrevendo” ou “falha”; o contrato do MCP ainda manda responder com estado no caso de reunião na fila. Deve ser explicitado como retornar e apresentar simultaneamente a revisão disponível e o estado da nova tentativa, preservando a leitura do resultado anterior.

6. **[IMPORTANTE] Falta fixar quando a configuração de uma revisão é congelada.**  
   Arquivos/requisitos: [transcript-store — Revisões](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/transcript-store/spec.md:31) e [settings-ui — Motor e vocabulário](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/settings-ui/spec.md:43).  
   A configuração pode mudar entre a transcrição do microfone e a do sistema, mas a revisão recebe uma única identificação de motor, configuração e vocabulário. É necessário definir o instante de captura desses valores e sua imutabilidade durante toda a tentativa, inclusive o que acontece após interrupção e retorno à fila.

7. **[IMPORTANTE] A suspensão exige uma finalização que pode não caber no prazo do Windows.**  
   Arquivo/requisitos: [recording-session — Finalização](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/recording-session/spec.md:71) e [Suspensão](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/recording-session/spec.md:123).  
   A finalização inclui compressão, mas o Windows concede aproximadamente dois segundos para tratar `PBT_APMSUSPEND`. [Documentação da Microsoft](https://learn.microsoft.com/en-us/windows/win32/power/pbt-apmsuspend). Deve ser definido o mínimo durável realizado antes da suspensão e a conclusão posterior no mesmo processo, pois a recuperação atual depende de nova inicialização e ausência do processo anterior.

8. **[IMPORTANTE] O encerramento por ociosidade não tem contrato com uma UI ainda conectada.**  
   Arquivos/requisitos: [recording-session — Ociosidade](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/recording-session/spec.md:15) e [desktop-shell — Ciclo de vida](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/desktop-shell/spec.md:33).  
   O serviço deve terminar após 300 segundos sem gravação nem fila, mesmo com a biblioteca aberta, enquanto o desktop só especifica conexão inicial e recuperação de queda. É necessário definir se clientes conectados impedem a saída ou como a UI reconhece o encerramento normal e reativa o serviço na próxima operação.

9. **[IMPORTANTE] O cursor de segmentos foi imposto a entidades que não possuem aquela ordenação.**  
   Arquivo/requisitos: [mcp-server — Paginação](E:/projetosAleatorios/VoxVault/openspec/changes/add-mcp-transcript-server/specs/mcp-server/spec.md:68) e [Leitura de notas](E:/projetosAleatorios/VoxVault/openspec/changes/add-mcp-transcript-server/specs/mcp-server/spec.md:131).  
   Todas as ferramentas volumosas devem usar cursor, mas a única chave definida é início/trilha/segmento: ela não resolve listagem de reuniões, busca mista nem continuação dentro de uma nota longa. Faltam ordenação, posição de continuação e política de invalidação para essas operações, especialmente quando uma nota é editada entre páginas.

10. **[IMPORTANTE] Uma captura em 1.800 ms passa num aceite e falha no outro.**  
    Arquivos/requisitos: [audio-capture — Atraso de início](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/audio-capture/spec.md:110) e [transcription-pipeline — Interrupção para gravar](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/transcription-pipeline/spec.md:29).  
    O primeiro exige 1.500 ms sem exceção; o segundo admite 2.000 ms quando interrompe transcrição. É preciso declarar a exceção ou unificar o teto, além de definir como medir o início do loopback quando ele está ocioso e não entrega amostras.

11. **[IMPORTANTE] A fase 3 declara opcional uma dependência que suas tarefas exigem.**  
    Arquivos/requisito: [add-desktop-app/proposal.md:30](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/proposal.md:30) e [tasks.md:51](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/tasks.md:51).  
    A fase 2 é declarada sem dependência técnica, mas cria o modelo e as operações de notas que o desktop obrigatoriamente consulta e modifica; também fornece o diagnóstico MCP exigido pela UI. Deve-se tornar a fase 2 pré-requisito ou definir explicitamente quais capacidades compartilhadas existem antes dela e quais recursos ficam condicionais.

12. **[IMPORTANTE] “Ação inválida para o estado” continua sem definir os estados em que apagar áudio é permitido.**  
    Arquivos/requisitos: [recording-session — Remoção de áudio](E:/projetosAleatorios/VoxVault/openspec/changes/add-meeting-recording-core/specs/recording-session/spec.md:229) e [library-ui — Ações sobre reunião](E:/projetosAleatorios/VoxVault/openspec/changes/add-desktop-app/specs/library-ui/spec.md:137).  
    Não há política para remover áudio enquanto a sessão grava, finaliza, aguarda transcrição ou está sendo reprocessada, nem para pedidos repetidos de reprocessamento. É necessário definir quais operações são recusadas, canceladas ou adiadas, com validação no serviço compartilhada pela CLI e pela UI.

13. **[IMPORTANTE] Fase 4: os sinais de detecção não estão vinculados ao mesmo aplicativo.**  
    Arquivos/requisito: [meeting-autodetect — Detecção de início](E:/projetosAleatorios/VoxVault/openspec/changes/add-recording-conveniences/specs/meeting-autodetect/spec.md:7) e [tasks.md:35](E:/projetosAleatorios/VoxVault/openspec/changes/add-recording-conveniences/tasks.md:35).  
    “Aplicativo de reunião aberto” combinado com “outro processo capturando microfone” permite detectar reunião com Teams ocioso e um gravador independente usando o microfone; navegadores ainda tornam a identidade da plataforma ambígua. Deve ser especificada a correlação entre proprietário da captura e aplicativo reconhecido, incluindo a política para navegadores e os valores padrão dos intervalos de detecção.

**Veredito**

**Não. As fases 0 a 3 ainda não estão prontas para implementação autônoma de ponta a ponta.** A avaliação humana deixou de ser bloqueante.

Restam bloqueantes técnicos: obter timestamps compatíveis com o alinhamento exigido, reconciliar durabilidade e fila, fechar a coordenação entre serviços e configurações, e completar os contratos de publicação/recuperação e leitura das revisões. Os problemas da fase 4 não entram nesse veredito.
