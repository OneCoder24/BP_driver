"""User-defined logic for post-pick actions."""
from __future__ import annotations
import logging
from typing import List
from API import RobotApi
from config import AdapterConfig, SessionState, set_gripper_state, BpPhase
from copy import copy

logger = logging.getLogger("RoboProSCAPE.UserLogic")

def add_xyz_offset(pose, offset_mm):
    return [
        pose[0] + offset_mm[0]/1000,
        pose[1] + offset_mm[1]/1000,
        pose[2] + offset_mm[2]/1000,
        pose[3],
        pose[4],
        pose[5]
    ]

def on_pick_success(robot: RobotApi, cfg: AdapterConfig, state: SessionState, task_payload: List[float]) -> bool:
    """Вызывается после успешного поднятия детали."""
    station = [-0.301730, -0.463649, -0.044821, 0.00, 0.00, -10]
    pallet = add_xyz_offset(copy(station), [-50, (state.count_of_parts // 10) * 60, 0])
    logger.info("🎯 Pick successful — user logic placeholder")
    
    # correction
    robot.motion.joint.add_new_waypoint(tcp_pose=add_xyz_offset(station, [0, 0, 200]))   
    robot.motion.mode.set("move")
    robot.motion.wait_waypoint_completion(0)
    
    robot.motion.linear.add_new_waypoint(tcp_pose=add_xyz_offset(station, [0, 0, 5]))   
    robot.motion.mode.set("move")
    robot.motion.wait_waypoint_completion(0)
    set_gripper_state(robot, False)
    
    robot.motion.linear.add_new_waypoint(tcp_pose=add_xyz_offset(station, [0, 0, 0]))   
    robot.motion.mode.set("move")
    robot.motion.wait_waypoint_completion(0)
    set_gripper_state(robot, True)
    
    robot.motion.linear.add_new_waypoint(tcp_pose=add_xyz_offset(station, [0, 0, 200]))   
    robot.motion.mode.set("move")
    robot.motion.wait_waypoint_completion(0)
    
    # pallet
    robot.motion.joint.add_new_waypoint(tcp_pose=add_xyz_offset(pallet, [0, 0, 200]))   
    robot.motion.mode.set("move")
    robot.motion.wait_waypoint_completion(0)
    
    robot.motion.linear.add_new_waypoint(tcp_pose=add_xyz_offset(pallet, [0, 0, (state.count_of_parts % 10) * 9 + 2]))   
    robot.motion.mode.set("move")
    robot.motion.wait_waypoint_completion(0)
    set_gripper_state(robot, False)
    
    robot.motion.linear.add_new_waypoint(tcp_pose=add_xyz_offset(pallet, [0, 0, 200]))   
    robot.motion.mode.set("move")
    robot.motion.wait_waypoint_completion(0)
    
    state.count_of_parts += 1
    #print(state.count_of_parts)
    
    # Возвращаем автомат в фазу ожидания следующего триггера
    state.bp_phase = BpPhase.WAIT_15
    return True

def on_pick_failure(robot: RobotApi, cfg: AdapterConfig, state: SessionState, task_payload: List[float]) -> bool:
    """Вызывается если деталь не найдена."""
    logger.warning("⚠️ Pick failed — user logic placeholder")
    set_gripper_state(robot, False)
    input("Do you want to continue? ")
    return True