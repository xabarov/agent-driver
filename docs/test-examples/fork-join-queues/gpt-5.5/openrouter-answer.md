 MPTCP, дата-центры, MapReduce, распределённые системы.
- [x] Выделить, какие характеристики рассчитывают с помощью этих моделей.
- [x] Собрать список полезных источников.
- [x] Сформулировать, как использовать fork-join модель для расчёта сети.

---

## 1. Что такое fork-join модель массового обслуживания

**Fork-join система массового обслуживания** — это модель, где входящая заявка / job / пакет / поток:

1. поступает в узел **fork**;
2. разбивается на несколько подзадач / фрагментов;
3. фрагменты параллельно обслуживаются на нескольких серверах / маршрутах / процессорах;
4. в узле **join** заявка считается завершённой только тогда, когда завершены все необходимые фрагменты.

Ключевая особенность:

$$
T_{\text{job}} = \max_{i=1,\dots,N} T_i
$$

где $T_i$ — время прохождения $i$-й подзадачи. Поэтому задержка всей заявки определяется самым медленным фрагментом.

В русскоязычной литературе такие модели часто называют:

- **СМО с делением и слиянием требований**;
- **СеМО с делением и слиянием требований**;
- **fork-join queueing systems / networks**.

Источник с хорошим русским описанием: статья о моделировании сетей передачи данных с многопутевой маршрутизацией через СеМО с делением и слиянием требований:
https://pandia.org/text/80/652/5052.php

---

## 2. Почему fork-join модели важны

Fork-join модели хорошо описывают системы, где есть **параллелизм + синхронизация**.

Примеры:

| Область | Интерпретация fork | Интерпретация join |
|---|---|---|
| Многопутевая маршрутизация | поток делится на подпотоки по разным путям | получатель собирает данные в правильном порядке |
| MPTCP | TCP-соединение делится на subflows | данные объединяются на принимающей стороне |
| MapReduce / Hadoop / Spark | задача делится на map-задачи | reduce ждёт завершения нужных map-задач |
| Дата-центры | запрос порождает параллельные RPC / микросервисные вызовы | ответ формируется после получения всех или части результатов |
| RAID / distributed storage | чтение / запись идёт по нескольким дискам или узлам | операция завершается после нужного числа ответов |
| Надёжная multicast / overlay-сеть | сообщение распространяется по нескольким ветвям | завершение зависит от доставки в набор узлов |

---

## 3. Найденные ключевые источники

| № | Источник | Что полезного |
|---:|---|---|
| 1 | **Computable Bounds in Fork-Join Queueing Systems**, ACM SIGMETRICS / Performance Evaluation Review | Даёт вычислимые стохастические границы задержек в fork-join системах; прямо упоминает MapReduce и multipath routing. URL: https://dl.acm.org/doi/10.1145/2745844.2745859 |
| 2 | **Stochastic Bounds in Fork-Join Queueing Systems**, PDF Warwick | Расширенная версия с описанием MapReduce, MPTCP, многопутевой маршрутизации, renewal / non-renewal arrivals, blocking / non-blocking servers. URL: https://wrap.warwick.ac.uk/id/eprint/79510/7/WRAP_stochastic_bounds_in_fork-join.pdf |
| 3 | **Optimizing Stochastic Scheduling in Fork-Join Queueing Models: Bounds and Applications**, arXiv | Оптимизация числа используемых серверов / путей, применение к MPTCP, MapReduce, RAID. URL: https://arxiv.org/abs/1612.05486 |
| 4 | **Scalability of fork/join queueing networks with blocking**, ACM SIGMETRICS 2007 | Применение к распределённым системам, TCP между компонентами, multicast, ad hoc networks. URL: https://dl.acm.org/doi/10.1145/1254882.1254898 |
| 5 | **Approximate analysis of fork/join synchronization in parallel queues**, Nelson & Tantawi, IEEE TC 1988 | Классическая работа по аппроксимации среднего времени отклика fork-join систем. URL: https://research.ibm.com/publications/approximate-analysis-of-forkjoin-synchronization-in-parallel-queues |
| 6 | **Performance evaluation of fork and join synchronization primitives**, Springer | Раннее моделирование fork-join примитивов в параллельных и распределённых системах. URL: https://link.springer.com/article/10.1007/BF00263293 |
| 7 | **Investigation of the Fork–Join System with MAP arrivals and PH service time using ML**, MDPI Mathematics 2024 | Современный подход: MAP-поток, PH-распределения, Монте-Карло + ML для оценки характеристик. URL: https://www.mdpi.com/2227-7390/12/5/659 |
| 8 | Русская статья: **Моделирование сетей передачи данных с многопутевой маршрутизацией СеМО с делением и слиянием требований** | Непосредственно про применение fork-join к сетям передачи данных и MPTCP. URL: https://pandia.org/text/80/652/5052.php |

---

## 4. Основные результаты из найденных работ

### 4.1. Точное решение часто сложно

Даже для сравнительно простых fork-join систем точный анализ труден. В ряде источников подчёркивается, что для $N > 2$ часто нет простого замкнутого решения.

Практически используют:

- аппроксимации;
- верхние и нижние границы;
- имитационное моделирование;
- фазовые распределения;
- Марковские модели;
- Монте-Карло;
- ML-аппроксимации.

---

### 4.2. Задержка растёт из-за синхронизации

Если заявка делится на $N$ подзадач, то время отклика определяется самой медленной подзадачей:

$$
T = \max(T_1, T_2, \dots, T_N)
$$

Поэтому увеличение числа параллельных ветвей даёт два противоположных эффекта:

1. **плюс**: больше параллелизма, меньше объём работы на одну ветвь;
2. **минус**: нужно ждать самый медленный фрагмент, появляется synchronization penalty.

В работах по stochastic bounds отмечается, что задержки в некоторых режимах масштабируются как $O(\log N)$.

---

### 4.3. Для многопутевой маршрутизации оптимально не всегда использовать много путей

В работе **Computable Bounds in Fork-Join Queueing Systems** сделан важный вывод для multipath routing:

> При умеренной и высокой загрузке многопутевая передача часто наиболее полезна при переходе с одного пути на два, но дальнейшее увеличение числа путей может ухудшать задержку из-за цены пересборки / resequencing delay.

Идея:

- при $N = 2$ выигрыш от параллельной передачи заметен;
- при большом $N$ задержка синхронизации и переупорядочивания начинает доминировать.

Это особенно важно для расчёта сетей с:

- MPTCP;
- ECMP;
- multipath routing;
- дата-центровыми topologies с несколькими disjoint paths.

---

## 5. Применение fork-join моделей для расчёта компьютерных сетей

### 5.1. Многопутевая маршрутизация

В сети с многопутевой маршрутизацией поток можно представить так:

```text
Источник
   |
 fork
 / | \
path1 path2 ... pathN
 \ | /
 join
   |
Получатель
```

Каждый путь — это отдельная СМО или цепочка СМО. Пакет / блок данных делится на фрагменты, которые идут по разным маршрутам. Получатель ждёт фрагменты и собирает исходные данные.

Рассчитываемые характеристики:

- среднее время доставки;
- хвостовые задержки, например $P(T > t)$;
- задержка пересборки;
- оптимальное число путей;
- загрузка маршрутов;
- вероятность переполнения буферов;
- пропускная способность.

---

### 5.2. MPTCP

В MPTCP одно логическое TCP-соединение делится на несколько subflows. Это хорошо ложится на fork-join модель:

- **fork**: отправитель делит данные на подпотоки;
- **parallel service**: подпотоки идут по разным маршрутам;
- **join**: получатель собирает поток в правильном порядке.

Работа на arXiv про оптимизацию stochastic scheduling прямо рассматривает MPTCP и выбор числа subflows:
https://arxiv.org/abs/1612.05486

Практический вопрос:

> Сколько путей / subflows использовать, чтобы минимизировать среднее время отклика или tail latency?

---

### 5.3. Дата-центровые сети

В дата-центрах один пользовательский запрос часто порождает множество параллельных внутренних запросов:

```text
frontend request
    |
 fork
 / | | \
service A B C D
 \ | | /
 join
    |
response to user
```

Если хотя бы один сервис отвечает медленно, весь запрос задерживается.

Fork-join модели применяются для оценки:

- tail latency;
- SLO / SLA;
- overprovisioning;
- числа серверов;
- загрузки кластера;
- влияния stragglers.

Источник про дата-центры и tail latency:
https://par.nsf.gov/servlets/purl/10157193

---

### 5.4. MapReduce, Hadoop, Spark

В MapReduce задача делится на map-подзадачи. Reduce-фаза часто ждёт завершения набора map-задач.

Модель:

```text
job arrival
   |
 fork into map tasks
 / / / /
workers
 \ \ \ \
 join before reduce
```

Здесь fork-join модель помогает оценить:

- время завершения job;
- влияние stragglers;
- оптимальное число workers;
- эффект репликации медленных задач;
- стоимость дополнительного параллелизма.

---

### 5.5. Надёжная передача, multicast, overlay, ad hoc

В работе ACM SIGMETRICS 2007 fork/join queueing networks with blocking мотивируются задачами:

- distributed stream processing;
- TCP между processing components;
- reliable multicast in overlay networks;
- reliable data transfer in ad hoc networks.

Источник:
https://dl.acm.org/doi/10.1145/1254882.1254898

---

## 6. Как practically построить fork-join модель для расчёта сети

Можно использовать такой алгоритм.

### Шаг 1. Определить входной поток

Например:

- пуассоновский поток с интенсивностью $\lambda$;
- renewal process;
- Markov Modulated Process для bursty traffic;
- поток из trace-данных.

Для Internet / data center traffic часто лучше использовать не простой Poisson, а bursty / Markov-modulated модель.

---

### Шаг 2. Определить правило деления

Например:

- каждая заявка делится на $N$ фрагментов;
- заявка делится только на $k$ из $N$ путей;
- используется redundancy: отправляем $n$ копий, ждём первые $k$ ответов;
- partial fork-join: используем только часть серверов / путей.

---

### Шаг 3. Задать модели обслуживания путей

Для каждого пути $i$:

- интенсивность обслуживания $\mu_i$;
- распределение времени обслуживания;
- размер буфера;
- вероятность потерь;
- дисциплина обслуживания: FIFO, Processor Sharing и т.п.

Простейшая модель:

$$
\rho_i = \frac{\lambda_i}{\mu_i}
$$

где $\rho_i$ — загрузка $i$-го пути.

---

### Шаг 4. Задать правило слияния

Варианты:

- ждать все $N$ фрагментов: классический fork-join;
- ждать любые $k$ из $N$: coded / redundant system;
- ждать первый ответ: replication model;
- ждать все фрагменты и учитывать resequencing delay.

---

### Шаг 5. Рассчитать метрики

Основные метрики:

| Метрика | Обозначение |
|---|---|
| Среднее время отклика | $E[T]$ |
| Вероятность большой задержки | $P(T > t)$ |
| $p$-квантиль задержки | $T_p$ |
| Пропускная способность | $\theta$ |
| Загрузка узлов | $\rho_i$ |
| Вероятность потери | $P_{\text{loss}}$ |
| Размер очередей | $E[Q_i]$ |
| Задержка пересборки | $D_{\text{reseq}}$ |

---

## 7. Выводы по теме

1. **Fork-join модели — естественный инструмент для анализа параллельной обработки и многопутевой передачи.**

2. В компьютерных сетях они особенно полезны для:
   - MPTCP;
   - multipath routing;
   - дата-центров;
   - MapReduce / Spark;
   - распределённого хранения;
   - multicast / overlay / ad hoc сетей.

3. Главная особенность модели — задержка всей заявки определяется медленнейшей ветвью:

   $$
   T_{\text{job}} = \max_i T_i
   $$

4. При увеличении числа путей появляется trade-off:
   - больше параллелизма;
   - но больше synchronization / resequencing penalty.

5. Для реальных расчётов часто используют не точные формулы, а:
   - computable bounds;
   - аппроксимации;
   - моделирование;
   - Монте-Карло;
   - ML-оценки;
   - trace-driven simulation.

6. Для многопутевых сетей важный практический вывод: **больше путей не всегда лучше**. В ряде моделей оптимальным или близким к оптимальному может быть использование малого числа путей, например $2$, потому что задержка пересборки быстро начинает доминировать.

---

## 8. Мини-библиография для дальнейшего чтения

1. **Baccelli, Makowski, Shwartz — The fork-join queue and related systems with synchronization constraints**
   https://www.cambridge.org/core/journals/advances-in-applied-probability/article/abs/forkjoin-queue-and-related-systems-with-synchronization-constraints-stochastic-ordering-and-computable-bounds/3265488EF6B8E36B2F11D17A2134A03D

2. **Nelson, Tantawi — Approximate analysis of fork/join synchronization in parallel queues**
   https://research.ibm.com/publications/approximate-analysis-of-forkjoin-synchronization-in-parallel-queues

3. **Computable Bounds in Fork-Join Queueing Systems**
   https://dl.acm.org/doi/10.1145/2745844.2745859

4. **Stochastic Bounds in Fork-Join Queueing Systems**
   https://wrap.warwick.ac.uk/id/eprint/79510/7/WRAP_stochastic_bounds_in_fork-join.pdf

5. **Optimizing Stochastic Scheduling in Fork-Join Queueing Models: Bounds and Applications**
   https://arxiv.org/abs/1612.05486

6. **Scalability of fork/join queueing networks with blocking**
   https://dl.acm.org/doi/10.1145/1254882.1254898

7. **Русскоязычный источник про многопутевую маршрутизацию и СеМО с делением/слиянием**
   https://pandia.org/text/80/652/5052.php