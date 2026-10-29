"""Cross-reference and structural validation at the independent audit boundary."""
from collections import Counter
from .contracts import (
    GridNetwork, ResourceCatalog, ServiceOrder, ExecutionPlan, _decode_typed,
    TICKS_PER_HOUR, operational_time_ticks,
)


def validate_problem_and_plan(network, services, catalog, plan):
    errors = []
    for name, value, kind in [('network', network, GridNetwork), ('catalog', catalog, ResourceCatalog),
                               ('plan', plan, ExecutionPlan)]:
        try:
            _decode_typed(value, kind, name)
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append(f'Contrato inválido: {exc}')
    for svc in services:
        try:
            _decode_typed(svc, ServiceOrder, 'service')
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append(f'Contrato inválido: {exc}')
    if errors:
        return errors
    if network.allow_dg_islanding:
        errors.append('Entrada não suportada: allow_dg_islanding=True exige modelo elétrico de ilhamento de GD não implementado.')
        return errors
    ticks = operational_time_ticks
    day_end = 24 * TICKS_PER_HOUR

    def check(condition, message):
        if not condition:
            errors.append(f'Estrutura/referência inválida: {message}')

    for collection, id_field in [(network.nodes, 'node_id'), (network.branches, 'branch_id'),
                                  (network.switches, 'switch_id'), (network.assets, 'asset_id'),
                                  (network.consumers, 'consumer_id'), (network.isolation_zones, 'zone_id'),
                                  (catalog.crews, 'crew_id')]:
        for key, item in collection.items():
            check(bool(key) and key == getattr(item, id_field), f'chave {key} diverge de {id_field}')
    for item in list(network.nodes.values()) + list(network.branches.values()) + list(network.assets.values()):
        check(bool(item.phases) and len(set(item.phases)) == len(item.phases) and
              set(item.phases) <= {'A', 'B', 'C'}, f'fases de {item}')
    for node in network.nodes.values():
        check(node.load_kw >= 0 and node.distributed_gen_kw >= 0 and node.nominal_voltage_kv > 0,
              f'parâmetros da barra {node.node_id}')
    for branch in network.branches.values():
        check(branch.from_node in network.nodes and branch.to_node in network.nodes and branch.from_node != branch.to_node,
              f'extremidades de {branch.branch_id}')
        check(branch.max_capacity_kw > 0 and branch.r_ohm >= 0 and branch.x_ohm >= 0 and branch.length_km >= 0,
              f'parâmetros de {branch.branch_id}')
    for switch in network.switches.values():
        check(switch.branch_id in network.branches, f'trecho da chave {switch.switch_id}')
        check(ticks(switch.maneuver_duration_hours) >= 0, f'duração de manobra {switch.switch_id}')
    for asset in network.assets.values():
        check(asset.node_id is not None or asset.branch_id is not None, f'ativo órfão {asset.asset_id}')
        check(asset.node_id is None or asset.node_id in network.nodes, f'barra de {asset.asset_id}')
        check(asset.branch_id is None or asset.branch_id in network.branches, f'trecho de {asset.asset_id}')
    for consumer in network.consumers.values():
        check(consumer.node_id in network.nodes and consumer.phase in {'A', 'B', 'C'}, f'consumidor {consumer.consumer_id}')
        check(consumer.asset_id is None or consumer.asset_id in network.assets, f'ativo de {consumer.consumer_id}')
        check(consumer.avg_demand_kw >= 0, f'demanda de {consumer.consumer_id}')
    for zone in network.isolation_zones.values():
        check(not (set(zone.open_switches) & set(zone.tie_switches_closed)), f'comandos contraditórios {zone.zone_id}')
        for seq in (zone.open_switches, zone.tie_switches_closed):
            check(len(seq) == len(set(seq)) and set(seq) <= network.switches.keys(), f'chaves de {zone.zone_id}')
        check(ticks(zone.prep_duration_hours) >= 0 and ticks(zone.recomp_duration_hours) >= 0 and zone.max_transfer_voltage_drop_pct >= 0,
              f'duração/limites da zona {zone.zone_id}')
        # Catalog assumes opening NF, closing NA, and restoring initial state at window end.
        for sid in zone.open_switches:
            if sid in network.switches:
                check(network.switches[sid].current_state.value == 'CLOSED', f'abertura sem mudança de estado {sid}')
        for sid in zone.tie_switches_closed:
            if sid in network.switches:
                check(network.switches[sid].current_state.value == 'OPEN', f'fechamento sem mudança de estado {sid}')
    svc_map = {s.service_id: s for s in services}
    check(len(svc_map) == len(services), 'IDs de serviços duplicados')
    for svc in services:
        check(svc.created_at_day <= svc.ready_at_day <= svc.deadline_day, f'datas de {svc.service_id}')
        check(ticks(svc.duration_hours) > 0 and ticks(svc.prep_hours) >= 0 and ticks(svc.recomp_hours) >= 0 and svc.required_crews_count >= 1,
              f'duração/equipes de {svc.service_id}')
        check(set(svc.precedence_service_ids) <= svc_map.keys() and svc.service_id not in svc.precedence_service_ids,
              f'precedências de {svc.service_id}')
        check(set(svc.compatible_zone_ids) <= network.isolation_zones.keys(), f'zonas de {svc.service_id}')
        check(set(svc.target_assets) <= network.assets.keys(), f'ativos de {svc.service_id}')
        check(all(q >= 0 for q in svc.required_materials.values()), f'materiais de {svc.service_id}')
    for crew in catalog.crews.values():
        check(0 <= ticks(crew.shift_start_hour) < ticks(crew.shift_end_hour) <= day_end
              and crew.speed_kmh > 0 and ticks(crew.prep_hours) >= 0,
              f'turno/velocidade de {crew.crew_id}')
    check(len(set(catalog.planning_days)) == len(catalog.planning_days), 'dias duplicados')
    check(ticks(catalog.window_end_hour) - ticks(catalog.window_start_hour) < day_end,
          'catálogo contínuo 24h sem gap diário positivo não suportado pelas colunas diárias')
    check(0 <= ticks(catalog.window_start_hour) < ticks(catalog.window_end_hour) <= day_end
          and ticks(catalog.max_window_duration_hours) > 0,
          'horizonte de horas')
    check(all(q >= 0 for q in catalog.materials_inventory.values()), 'estoque negativo')
    check(network.valid_from_day <= plan.decision_day, 'rede posterior à decisão')
    check(plan.solver_time_sec >= 0 and (plan.solver_gap is None or plan.solver_gap >= 0) and
          (plan.solver_objective is None or plan.solver_objective >= 0), 'valores negativos do solver')
    win_ids = [w.window_id for w in plan.windows]
    check(len(set(win_ids)) == len(win_ids), 'IDs de janelas duplicados')
    scheduled = [sid for w in plan.windows for sid in w.service_ids]
    for sid in scheduled:
        if sid in svc_map and svc_map[sid].created_at_day > plan.decision_day:
            errors.append(f"Vazamento temporal: serviço '{sid}' criado após a decisão.")
    # Derived portfolio fields are evidence too, never accepted merely on trust.
    check(Counter(plan.completed_service_ids) == Counter(scheduled), 'completed_service_ids diverge das janelas')
    eligible = {s.service_id for s in services if s.scope_status == 'ACTIVE' and s.created_at_day <= plan.decision_day}
    check(set(plan.unscheduled_service_ids) == eligible - set(scheduled) and
          len(set(plan.unscheduled_service_ids)) == len(plan.unscheduled_service_ids), 'unscheduled_service_ids diverge da carteira')
    for win in plan.windows:
        check(0 <= ticks(win.start_hour) <= ticks(win.work_start_hour) < ticks(win.work_end_hour) <= ticks(win.end_hour) <= day_end,
              f'cronologia da janela {win.window_id}')
        check(win.day >= plan.decision_day, f'janela anterior à decisão {win.window_id}')
        for iv in win.crew_intervals:
            check(iv.service_id in win.service_ids and iv.crew_id in catalog.crews and
                  iv.window_id == win.window_id and iv.day == win.day, f'intervalo órfão em {win.window_id}')
            check(0 <= ticks(iv.travel_start_hour) <= ticks(iv.prep_start_hour) <= ticks(iv.exec_start_hour)
                  < ticks(iv.exec_end_hour) <= ticks(iv.recomp_end_hour) <= day_end
                  and ticks(iv.travel_hours) >= 0, f'cronologia de equipe {iv.crew_id}')
        pairs = [(iv.crew_id, iv.service_id) for iv in win.crew_intervals]
        check(len(set(pairs)) == len(pairs), f'alocações duplicadas em {win.window_id}')
        expected_materials = {}
        for sid in win.service_ids:
            if sid in svc_map:
                for mat, qty in svc_map[sid].required_materials.items():
                    expected_materials[mat] = expected_materials.get(mat, 0) + qty
        check(set(win.materials_used) == set(expected_materials) and
              all(abs(win.materials_used[k] - v) <= 1e-4 for k, v in expected_materials.items()),
              f'materiais exportados de {win.window_id}')
        for step in win.maneuver_steps:
            check(0 <= ticks(step.start_hour) <= ticks(step.end_hour) <= day_end and step.max_loading_pct >= 0 and
                  step.estimated_voltage_drop_pct >= 0, f'etapa elétrica {win.window_id}')
            for name in ('open_switches', 'closed_switches', 'energized_nodes', 'deenergized_nodes',
                         'deenergized_assets', 'interrupted_consumers', 'active_sources'):
                seq = getattr(step, name)
                check(len(seq) == len(set(seq)), f'IDs duplicados em {win.window_id}.{name}')
    return errors
