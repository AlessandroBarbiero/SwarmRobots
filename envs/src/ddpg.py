
import numpy as np

import torch
import torch.nn as nn
from torch.optim import Adam

from .model import (Actor, Critic)
from .memory import SequentialMemory
from .random_process import OrnsteinUhlenbeckProcess
from .util import *

import os

# from ipdb import set_trace as debug

criterion = nn.MSELoss()

class DDPG(object):
    def __init__(self, nb_states, nb_actions):
        
        self.nb_states = nb_states
        self.nb_actions= nb_actions

        self.hidden1 = 256
        self.hidden2 = 256
        self.init_w = 0.003
        self.prate = 0.0001
        self.rate = 0.001
        self.rmsize = 6000000
        self.window_length = 1
        self.ou_theta = 0.15
        self.ou_mu = 0.0
        self.ou_sigma = 0.2

        self.batch_size =64
        self.tau = 0.001
        self.discount = 0.99
        self.depsilon = 1.0 / 50000
        
        # Create Actor and Critic Network
        net_cfg = {
            'hidden1':self.hidden1, 
            'hidden2':self.hidden2, 
            'init_w':self.init_w
        }
        self.actor = Actor(self.nb_states, self.nb_actions, **net_cfg)
        self.actor_target = Actor(self.nb_states, self.nb_actions, **net_cfg)
        self.actor_optim  = Adam(self.actor.parameters(), lr=self.prate)

        self.critic = Critic(self.nb_states, self.nb_actions, **net_cfg)
        self.critic_target = Critic(self.nb_states, self.nb_actions, **net_cfg)
        self.critic_optim  = Adam(self.critic.parameters(), lr=self.rate)

        hard_update(self.actor_target, self.actor) # Make sure target is with the same weight
        hard_update(self.critic_target, self.critic)
        
        #Create replay buffer
        self.memory = SequentialMemory(limit=self.rmsize, window_length=self.window_length)
        self.random_process = OrnsteinUhlenbeckProcess(size=nb_actions, theta=self.ou_theta, mu=self.ou_mu, sigma=self.ou_sigma)

        

        
        self.epsilon = 1.0
        # self.s_t = None # Most recent state
        # self.a_t = None # Most recent action
        self.is_training = True

        self.recent_state = {}
        self.recent_action = {}

        # 
        if USE_CUDA: self.cuda()

    def initialize_states_actions(self, decision_steps):
        for agent_id in decision_steps:
            self.recent_state[agent_id] = None
            self.recent_action[agent_id] = None

    def update_policy(self):
        # Sample batch
        state_batch, action_batch, reward_batch, \
        next_state_batch, terminal_batch = self.memory.sample_and_split(self.batch_size)

        # Prepare for the target q batch
        with torch.no_grad():
            next_q_values = self.critic_target([
                to_tensor(next_state_batch, volatile=True),
                self.actor_target(to_tensor(next_state_batch, volatile=True)),
            ])
        # next_q_values.volatile=False

        target_q_batch = to_tensor(reward_batch) + \
            self.discount*to_tensor(terminal_batch.astype(np.float))*next_q_values #output of target critic - yi

        # Critic update
        self.critic.zero_grad()

        q_batch = self.critic([ to_tensor(state_batch), to_tensor(action_batch) ]) #output of critic - Q(si,ai)
        
        value_loss = criterion(q_batch, target_q_batch)
        value_loss.backward()

        self.critic_optim.step()

        # Actor update
        self.actor.zero_grad()

        policy_loss = -self.critic([
            to_tensor(state_batch),
            self.actor(to_tensor(state_batch))
        ]) #update of the actor



        policy_loss = policy_loss.mean() #TODO understand
        policy_loss.backward()

        self.actor_optim.step()

        # Target update
        soft_update(self.actor_target, self.actor, self.tau)
        soft_update(self.critic_target, self.critic, self.tau)

    def eval(self):
        self.actor.eval()
        self.actor_target.eval()
        self.critic.eval()
        self.critic_target.eval()

    def cuda(self):
        self.actor.cuda()
        self.actor_target.cuda()
        self.critic.cuda()
        self.critic_target.cuda()

    def observe(self, agent_id, r_t, s_t1, done):
        if self.is_training:
            self.memory.append(self.recent_state[agent_id], self.recent_action[agent_id], r_t, done)
            self.update_obs(agent_id,s_t1)

    def random_action(self, num_agents):
        action = np.random.uniform(-1.,1.,(num_agents,self.nb_actions))
        # self.a_t = action
        
        self.update_recent_actions(action)
        return action

    def select_action(self, s_t, decay_epsilon=True):
        
        action = to_numpy(
            self.actor(to_tensor(np.array([s_t])))
        ).squeeze(0)
        action += self.is_training*max(self.epsilon, 0)*self.random_process.sample()
        action = np.clip(action, -1., 1.)

        if decay_epsilon:
            self.epsilon -= self.depsilon
        
        self.update_recent_actions(action)

        return action
    
    def update_recent_actions(self, action):
        i = 0
        for agent_id in self.recent_action:
            self.recent_action[agent_id] = action[i,:]
            i += 1

    def update_obs(self, agent, obs):
        self.recent_state[agent] = normalize_state(obs)
        
    def reset_random_process(self):
        self.random_process.reset_states()

    def load_weights(self, file_to_save,env_name):
        file_to_save += "//data"

        self.actor.load_state_dict(
            torch.load('{}/actor_{}.pkl'.format(file_to_save,env_name))
        )

        self.critic.load_state_dict(
            torch.load('{}/critic_{}.pkl'.format(file_to_save,env_name))
        )

    def save_model(self,file_to_save,env):
        file_to_save += "//data"
        torch.save(
            self.actor.state_dict(),
            '{}/actor_{}.pkl'.format(file_to_save,env)
        )
        torch.save(
            self.critic.state_dict(),
            '{}/critic_{}.pkl'.format(file_to_save,env)
        )
    
    