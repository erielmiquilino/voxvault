## 1. Ambiente e esqueleto do projeto

- [ ] 1.1 Criar o pacote `voxvault-core` com `pyproject.toml` fixando `requires-python = ">=3.12,<3.13"`, e verificar que `uv sync` cria a venv em 3.12.12 e que `uv run python -V` imprime uma versão 3.12 — o teto é desta fase e será reconfirmado ou revisado no portão de captura da Fase 1
- [ ] 1.2 Instalar ffmpeg na máquina e verificar que `ffmpeg -version` responde a partir do `PATH`
- [ ] 1.3 Declarar as dependências de runtime (`faster-whisper`, `ctranslate2`, `soxr`, `numpy`, `typer`, `rich`) e as de GPU (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`), e verificar que `uv sync` resolve sem conflito
- [ ] 1.4 Configurar `pytest` e `ruff`, e verificar que `uv run pytest` e `uv run ruff check` rodam num repositório sem testes ainda, terminando com sucesso
- [ ] 1.5 Criar o módulo de configuração compartilhada em local fixo por usuário, com a precedência parâmetro > ambiente > arquivo > padrão embutido (`D:\VoxVault`), apontando o cache de modelos para dentro do diretório de dados; verificar por teste unitário que cada nível da precedência prevalece sobre o seguinte
- [ ] 1.5.1 Garantir que a resolução do diretório de dados não depende do diretório de trabalho do processo, e verificar por teste executando a resolução a partir de dois diretórios de trabalho distintos e obtendo o mesmo resultado
- [ ] 1.5.2 Implementar o registro da origem de cada valor efetivo de configuração e a falha explícita com nome do campo e da fonte quando um valor é inválido; verificar por testes dos dois comportamentos
- [ ] 1.6 Adicionar `.gitignore` cobrindo venv, caches e artefatos de build, e verificar que `git status` fica limpo após um `uv sync`

## 2. Diagnóstico de ambiente

- [ ] 2.1 Implementar as checagens individuais (versão de Python, GPU e VRAM, carregamento real das bibliotecas de inferência acelerada, ffmpeg no `PATH`, diretório de dados gravável e espaço livre), cada uma devolvendo estado `ok`/`aviso`/`falha` com mensagem corretiva; verificar por testes unitários com as dependências simuladas que cada estado é atingível
- [ ] 2.2 Garantir que a checagem das bibliotecas de GPU executa uma inferência mínima real, e não apenas constata a presença do pacote instalado, e verificar por teste que um carregamento que lança erro produz estado `falha` com mensagem que distingue instalação de carregamento
- [ ] 2.2.1 Garantir que o diagnóstico roda no mesmo interpretador, variáveis de ambiente e diretório de trabalho que a transcrição usará, e verificar por teste que um ambiente de lançamento divergente é detectado em vez de mascarado
- [ ] 2.2.2 Implementar a matriz de disponibilidade por capacidade, com cada pré-requisito declarando o que condiciona; verificar por testes que cobrem as cinco linhas da matriz — GPU ok, sem GPU no hardware, GPU com bibliotecas quebradas, modelo maior que a memória, e decodificador ausente
- [ ] 2.2.3 Implementar a configuração que habilita execução em CPU mesmo com GPU presente e bibliotecas quebradas, desativada por padrão, e verificar por teste que sem ela a transcrição é recusada e com ela é permitida com aviso a cada execução
- [ ] 2.2.4 Verificar por teste que a gravação e a importação permanecem disponíveis quando apenas os pré-requisitos de transcrição estão ausentes
- [ ] 2.3 Expor o comando `voxvault doctor` que executa todas as checagens, imprime o relatório e sai com código zero apenas quando não há nenhuma `falha`; verificar executando o comando na máquina e conferindo o código de saída
- [ ] 2.4 Implementar a criação automática do diretório de dados quando ausente, incluindo diretórios pais, e verificar por teste em diretório temporário
- [ ] 2.5 Implementar a barreira de pré-requisitos que aborta qualquer operação antes de carregar modelo ou ler áudio quando um pré-requisito obrigatório falta, e verificar por teste que nenhum arquivo é criado no diretório de dados nesse caminho
- [ ] 2.6 Rodar `voxvault doctor` na máquina alvo e resolver o que aparecer até todos os itens ficarem `ok`, registrando no README os passos que foram necessários

## 3. Contrato e implementação de transcrição

- [ ] 3.1 Definir o `Protocol` `TranscriptionEngine` e o tipo `Segment` (início e fim em milissegundos inteiros, texto), mais a estrutura de identificação de motor e configuração; verificar com `mypy` ou equivalente que uma implementação de teste satisfaz o `Protocol`
- [ ] 3.2 Definir a hierarquia de erros tipados cobrindo entrada ilegível, pré-requisito ausente, recurso de hardware insuficiente e falha de provedor, e verificar por teste que cada categoria é distinguível pelo consumidor
- [ ] 3.3 Implementar a normalização de áudio via ffmpeg para 16 kHz mono, escrevendo em arquivo temporário e preservando o original; verificar por teste com um mp3 e um mp4 de amostra que a saída tem o formato esperado e que o arquivo de entrada permanece byte a byte idêntico
- [ ] 3.4 Implementar `FasterWhisperEngine` sobre CUDA em `float16`, com `vad_filter` ativo e `condition_on_previous_text` desligado, e verificar transcrevendo um áudio curto real que segmentos com texto são produzidos
- [ ] 3.5 Implementar a política de execução seguindo a matriz de disponibilidade — GPU em `float16`, CPU em `int8` apenas quando não há GPU no hardware ou quando a execução em CPU foi habilitada explicitamente, e recusa quando há GPU com bibliotecas quebradas; verificar por testes que cobrem os três caminhos com a detecção simulada
- [ ] 3.5.1 Garantir que o identificador de motor e configuração registra dispositivo, precisão e modelo efetivamente usados, e verificar por teste que uma execução em CPU é distinguível de uma em GPU pelo identificador
- [ ] 3.6 Implementar o erro de memória de GPU insuficiente nomeando modelo, memória exigida e disponível, e verificar por teste com a consulta de memória simulada
- [ ] 3.7 Implementar a passagem do vocabulário de domínio como `initial_prompt` e registrá-lo na identificação da configuração; verificar por teste que o vocabulário chega ao motor e aparece no identificador
- [ ] 3.8 Garantir as invariantes dos segmentos (início não negativo, fim maior que início, ordem não decrescente, texto não vazio) por validação na saída do motor, e verificar com teste de propriedade sobre segmentos gerados
- [ ] 3.9 Verificar, com um áudio de silêncio de pelo menos dois minutos, que a transcrição devolve sequência vazia e não produz texto inventado
- [ ] 3.10 Implementar a configuração padrão do motor (`large-v3`, `float16`, GPU, idioma `pt`) aplicada quando nenhuma outra é indicada, e verificar por teste que uma transcrição sem configuração explícita usa esse padrão e o registra no resultado

## 4. Harness de benchmark

- [ ] 4.1 Implementar o carregamento da lista de configurações a comparar a partir de um arquivo de configuração, e verificar por teste que duas configurações de modelo distintas são lidas corretamente
- [ ] 4.2 Implementar a execução sequencial das configurações sobre a mesma entrada normalizada uma única vez, e verificar por teste que todas recebem exatamente o mesmo arquivo normalizado
- [ ] 4.3 Implementar a medição de tempo de carregamento, tempo de decodificação, fator de tempo real e pico de memória de GPU por execução; verificar rodando duas configurações e conferindo que os valores são plausíveis e distintos
- [ ] 4.4 Implementar a liberação de memória de GPU entre execuções e verificar, com monitoramento durante um benchmark de três configurações, que a memória volta ao patamar inicial entre elas
- [ ] 4.5 Implementar o isolamento de falha por execução, registrando o motivo e seguindo para a próxima, com código de saída diferente de zero ao final; verificar por teste com uma configuração propositalmente inválida
- [ ] 4.6 Implementar o alinhamento dos textos por janela de tempo, e não por índice de segmento, e verificar por teste com duas sequências de segmentação diferente que nenhum texto é descartado
- [ ] 4.7 Implementar a geração do relatório comparativo em Markdown, com tabela-resumo de desempenho no topo e corpo em colunas alinhadas por instante; verificar abrindo o relatório de uma execução real
- [ ] 4.8 Implementar a gravação das transcrições brutas por execução sob o diretório de dados, identificadas por instante e arquivo de entrada, sem sobrescrever execuções anteriores; verificar rodando o benchmark duas vezes sobre a mesma entrada e conferindo que ambos os conjuntos sobrevivem
- [ ] 4.9 Expor o comando `voxvault benchmark` recebendo o arquivo de entrada e o arquivo de configurações, e verificar executando-o de ponta a ponta

## 5. Medição e portão de decisão

> As tarefas 5.1 e 5.5 **dependem do usuário** e não podem ser concluídas por um agente: uma exige material gravado por ele, a outra é o julgamento de qualidade que só cabe a ele. As demais são executáveis de forma autônoma. Nenhuma tarefa desta seção bloqueia as fases seguintes, porque a configuração padrão de motor já está definida em 3.10.

- [ ] 5.1 **(usuário)** Fornecer de 10 a 15 minutos de áudio de reunião real, com ruído e mais de um interlocutor, e verificar que o arquivo é legível pelo pipeline de normalização
- [ ] 5.2 Executar o benchmark comparando `large-v3` e `large-v3-turbo` em `float16` na GPU sobre o áudio fornecido, e verificar que o relatório e as transcrições brutas de ambos foram gerados
- [ ] 5.3 Registrar no relatório os números medidos de tempo, fator de tempo real e pico de VRAM, substituindo as estimativas do projeto por valores reais
- [ ] 5.4 Produzir o relatório comparativo lado a lado pronto para leitura, com os dois textos alinhados por janela de tempo, e verificar que ele abre e é legível
- [ ] 5.5 **(usuário)** Ler a comparação e decidir entre manter a transcrição local ou passar para provedor online, registrando a decisão e o modelo escolhido em `openspec/changes/setup-transcription-benchmark/decision.md`
- [ ] 5.6 Após a decisão do usuário, ajustar a configuração padrão do motor para refletir o modelo escolhido, e verificar por teste que a nova configuração passa a ser aplicada sem alteração em nenhum consumidor do contrato
